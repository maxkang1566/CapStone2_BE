"""인스타그램 크롤링 통합 파이프라인.

흐름:
1. URL에서 shortcode 추출
2. place_raw_data(provider='instagram', provider_place_id=shortcode) 조회 → hit 시 즉시 정규화 응답
3. miss 시 Apify 호출 시도 (월 비용 한도 체크 포함)
4. Apify 실패/한도 초과 시 OG 메타 fallback (기존 InstagramCrawler)
5. 결과를 raw로 저장하고 정규화하여 반환

캐시 저장소가 별도 instagram_post_cache 테이블이 아니라 place_raw_data로 통합된 이유:
인스타 raw를 사용자에게 보여줄 정제 데이터(places/spots)와 같은 1차 소스로 다루기 위함.
저장 시점에는 place_id=NULL로 두고, 정제 단계(spot_creator)에서 매핑된 place_id를 UPDATE로 채운다.

모든 외부 호출은 동기적으로 일어나며, 비동기 라우터 컨텍스트에서는
워커(`app.services.instagram_jobs.process_crawl_job`)에서 호출하는 것을 가정한다.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func as sa_func, text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.models import InstagramCrawlJob, PlaceRawData
from app.schemas.instagram import InstagramCrawlResponse
from app.services import apify_client
from app.services.apify_client import (
    ApifyEmptyResultError,
    ApifyError,
)
from app.services.instagram_crawler import InstagramCrawler
from app.services.playwright_manager import PlaywrightManager

# 인스타 URL 패턴: /p/{shortcode}/, /reel/{shortcode}/, /tv/{shortcode}/
_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")

# Apify 호출 1건당 추정 비용(USD). 액터·옵션에 따라 다르며 가드용 근사치.
_ESTIMATED_APIFY_COST_PER_CALL = 0.002

# Apify가 인스타 차단으로 `error: "restricted_page"` 페이로드를 반환한 경우의 캐시 TTL.
# 인스타는 시기적으로 차단을 풀고 잠그므로, 영구 캐시하면 차단 해제 후에도 빈약한 데이터가
# 영원히 hit된다. 정상 캐시(완전한 caption/이미지/위치)는 TTL을 두지 않는다.
_RESTRICTED_TTL_SECONDS = 6 * 3600


class PipelineError(Exception):
    """파이프라인 처리 실패."""


@dataclass
class CachedPost:
    """get_cached가 반환하는 캐시 표현. raw_payload 안에 묻힌 메타키(_source, _url)를
    풀어 명시 필드로 노출해 호출처가 PlaceRawData 내부 구조에 직접 의존하지 않도록 한다.
    """
    payload: dict           # 메타키(_source, _url) 제거된 정규화 payload
    source: str             # 'apify' | 'og_fallback'
    collected_at: datetime
    url: Optional[str] = None  # 메타키 _url 복원 (필요 시)


def extract_shortcode(url: str) -> Optional[str]:
    """인스타 URL에서 게시물 shortcode를 추출한다."""
    if not url:
        return None
    m = _SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def _split_meta_keys(raw_payload: Optional[dict]) -> tuple[dict, str, Optional[str]]:
    """raw_payload에서 메타키를 분리해 (clean_payload, source, url) 튜플로 반환한다.

    save_cache가 raw_payload에 _source/_url을 주입해두고, get_cached에서 이를 풀어낸다.
    save_cache 이전 형태(메타키 없음)도 안전하게 처리: source 기본값은 'apify'.
    """
    if not raw_payload:
        return {}, "apify", None
    payload = dict(raw_payload)
    source = payload.pop("_source", "apify")
    url = payload.pop("_url", None)
    return payload, source, url


def _is_restricted_expired(row: PlaceRawData, source: str) -> bool:
    """restricted_page 응답 캐시가 TTL을 넘겼는지 판정한다.
    정상 캐시(restricted가 아니거나 source != 'apify')는 항상 False(만료 없음).
    """
    if source != "apify":
        return False
    payload = row.raw_payload or {}
    if payload.get("error") != "restricted_page":
        return False
    collected = row.collected_at
    if collected is None:
        return True
    # DB가 naive datetime을 줄 수 있으니 UTC로 보정해서 비교
    if collected.tzinfo is None:
        collected = collected.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - collected
    return age >= timedelta(seconds=_RESTRICTED_TTL_SECONDS)


def get_cached(db: Session, shortcode: str) -> Optional[CachedPost]:
    """shortcode로 캐시된 인스타 raw를 조회한다.

    restricted_page 응답이 TTL을 넘긴 경우는 None을 반환해 fetch_post가 재크롤하도록 한다.
    `save_cache`가 UPSERT라 만료된 행을 미리 지울 필요 없음.
    """
    row = (
        db.query(PlaceRawData)
        .filter(
            PlaceRawData.provider == "instagram",
            PlaceRawData.provider_place_id == shortcode,
        )
        .first()
    )
    if row is None:
        return None

    clean_payload, source, url = _split_meta_keys(row.raw_payload)
    if _is_restricted_expired(row, source):
        logger.info(
            "restricted_page 캐시(shortcode=%s)가 TTL을 넘겨 재크롤 대상으로 처리합니다.",
            shortcode,
        )
        return None
    return CachedPost(
        payload=clean_payload,
        source=source,
        collected_at=row.collected_at,
        url=url,
    )


def save_cache(db: Session, shortcode: str, url: str, payload: dict, source: str) -> None:
    """인스타 raw 응답을 place_raw_data에 UPSERT한다.

    저장 형태:
      provider='instagram', provider_place_id=shortcode, place_id=NULL(아직 정제 전),
      raw_payload={..원본 전체.., '_source': source, '_url': url}

    ON CONFLICT 처리:
      - 부분 유니크 인덱스 `uq_place_raw_data_provider_pid`(provider, provider_place_id)
        WHERE provider_place_id IS NOT NULL을 conflict target으로 사용
      - DO UPDATE에는 raw_payload, collected_at만 갱신하고 **place_id는 보존한다**
        (정제 단계에서 채워진 연결을 재크롤이 NULL로 덮어쓰면 안 되므로)
      - collected_at=NOW()로 갱신해 restricted TTL 기준점을 새로 잡는다.

    트랜잭션 경계: 자체 commit. 후속 share_post 흐름이 IntegrityError로 롤백돼도 raw 행은
    살아있고, 다음 시도 시 UPSERT로 동일 행을 갱신한다.
    """
    enriched_payload = dict(payload)
    enriched_payload["_source"] = source
    enriched_payload["_url"] = url

    stmt = pg_insert(PlaceRawData).values(
        place_id=None,
        provider="instagram",
        provider_place_id=shortcode,
        raw_payload=enriched_payload,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["provider", "provider_place_id"],
        index_where=sa_text("provider_place_id IS NOT NULL"),
        set_={
            "raw_payload": stmt.excluded.raw_payload,
            "collected_at": sa_func.now(),
            # ★ place_id는 set_에 포함하지 않음 — 정제 단계가 채운 연결 보존
        },
    )
    db.execute(stmt)
    db.commit()


def _is_apify_budget_exceeded(db: Session) -> bool:
    """이번 달 Apify 호출 카운트 × 추정 단가가 환경변수 한도를 넘는지 확인한다."""
    budget_str = os.getenv("APIFY_MONTHLY_BUDGET_USD")
    if not budget_str:
        return False
    try:
        budget = float(budget_str)
    except ValueError:
        return False
    if budget <= 0:
        return True

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = (
        db.query(sa_func.count(InstagramCrawlJob.id))
        .filter(
            InstagramCrawlJob.source == "apify",
            InstagramCrawlJob.created_at >= month_start,
        )
        .scalar()
        or 0
    )
    estimated_cost = count * _ESTIMATED_APIFY_COST_PER_CALL
    return estimated_cost >= budget


def _normalize_apify(url: str, raw: dict) -> InstagramCrawlResponse:
    """Apify 응답을 InstagramCrawlResponse로 정규화한다.

    액터 버전·옵션에 따라 키가 미묘하게 다를 수 있어 다중 키를 안전하게 시도한다.
    인스타가 비로그인 요청을 막아 `error: "restricted_page"`로 응답하면 키가 통째로
    달라지므로(`description`/`image`만 채워짐) 그 케이스도 별도로 매핑한다.
    """
    # 인스타 차단 응답: caption/images/location 키가 없고 description/image만 있다.
    if raw.get("error") == "restricted_page":
        image = raw.get("image")
        return InstagramCrawlResponse(
            url=url,
            caption=raw.get("description"),
            images=[image] if image else [],
            location_name=None,
            instagram_location_id=None,
            latitude=None,
            longitude=None,
            hashtags=[],
            mentions=[],
            posted_at=None,
            owner_username=None,
            og_title=raw.get("title"),
            og_description=raw.get("description"),
        )

    images = list(raw.get("images") or [])
    if not images:
        display = raw.get("displayUrl")
        if display:
            images = [display]

    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}

    location_name = raw.get("locationName") or loc.get("name")
    location_id = raw.get("locationId") or loc.get("id")
    latitude = raw.get("latitude") or loc.get("lat") or loc.get("latitude")
    longitude = raw.get("longitude") or loc.get("lng") or loc.get("longitude")

    return InstagramCrawlResponse(
        url=url,
        caption=raw.get("caption"),
        images=list(images),
        location_name=location_name,
        instagram_location_id=str(location_id) if location_id is not None else None,
        latitude=latitude,
        longitude=longitude,
        hashtags=list(raw.get("hashtags") or []),
        mentions=list(raw.get("mentions") or []),
        posted_at=raw.get("timestamp"),
        owner_username=raw.get("ownerUsername"),
        og_title=None,
        og_description=None,
    )


def _normalize_og(url: str, raw: dict) -> InstagramCrawlResponse:
    """OG fallback 응답(이미 InstagramCrawler가 만든 형태)을 정규화한다."""
    return InstagramCrawlResponse(
        url=url,
        caption=raw.get("caption"),
        images=list(raw.get("images") or []),
        location_name=raw.get("location_name"),
        instagram_location_id=raw.get("instagram_location_id"),
        latitude=None,
        longitude=None,
        hashtags=[],
        mentions=[],
        posted_at=None,
        owner_username=None,
        og_title=raw.get("og_title"),
        og_description=raw.get("og_description"),
    )


def _run_og_fallback(url: str, manager: Optional[PlaywrightManager]) -> dict:
    """기존 OG 크롤러를 호출해 dict 형태로 응답을 반환한다."""
    if manager is None or manager.browser is None:
        raise PipelineError(
            "Apify 호출이 실패했고 OG fallback용 PlaywrightManager도 사용 불가합니다."
        )
    crawler = InstagramCrawler(manager=manager)
    resp = crawler.crawl_post(url)
    return {
        "url": str(resp.url),
        "caption": resp.caption,
        "images": list(resp.images or []),
        "location_name": resp.location_name,
        "instagram_location_id": resp.instagram_location_id,
        "og_title": resp.og_title,
        "og_description": resp.og_description,
    }


def fetch_post(
    url: str,
    db: Session,
    *,
    playwright_manager: Optional[PlaywrightManager] = None,
) -> tuple[InstagramCrawlResponse, str]:
    """크롤링 진입점. 정규화된 응답과 source('apify' | 'og_fallback' | 'cache_apify' | 'cache_og_fallback')를 반환한다."""
    shortcode = extract_shortcode(url)
    if not shortcode:
        raise PipelineError("인스타그램 게시물 URL에서 shortcode를 추출하지 못했습니다.")

    # 1) 캐시 조회
    cached = get_cached(db, shortcode)
    if cached:
        if cached.source == "apify":
            return _normalize_apify(url, cached.payload), "cache_apify"
        return _normalize_og(url, cached.payload), "cache_og_fallback"

    # 2) Apify 호출 (월 한도 체크)
    if _is_apify_budget_exceeded(db):
        logger.warning("Apify monthly budget exceeded — skipping to OG fallback")
    else:
        try:
            payload = apify_client.run_instagram_post_scraper(url)
            save_cache(db, shortcode, url, payload, source="apify")
            return _normalize_apify(url, payload), "apify"
        except ApifyEmptyResultError as e:
            logger.warning("Apify returned empty result for %s: %s", url, e)
        except ApifyError as e:
            logger.warning("Apify call failed for %s: %s", url, e)

    # 3) OG fallback
    og_payload = _run_og_fallback(url, playwright_manager)
    save_cache(db, shortcode, url, og_payload, source="og_fallback")
    return _normalize_og(url, og_payload), "og_fallback"
