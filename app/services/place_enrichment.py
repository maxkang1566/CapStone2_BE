"""Place enrichment: 네이버 블로그 검색 + 본문 크롤링 → place_raw_data + place_reviews 적재.

`/instagram/share` 자동 저장 분기와 `/instagram/save` 수동 저장 분기 모두에서
새 Place가 만들어진 직후 `enqueue_blog_fetch_job`로 트리거한다.

워커는 `app.services.instagram_jobs.process_blog_fetch_job`이 본 모듈의
`fetch_and_persist_blog_reviews`를 호출하는 구조.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func as sa_func, text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.models import (
    InstagramCrawlJob,
    Place,
    PlaceRawData,
    PlaceReview,
)
from app.services import naver_blog_body_fetcher, naver_blog_search
from app.services.naver_blog_search import NaverBlogSearchError

logger = logging.getLogger(__name__)

# 30일 이내 수집된 행이 있으면 갱신 스킵.
_REFRESH_DAYS = 30

# 본문 fetch 실패율이 이 이상이면 차단으로 판정해 status='failed'로 마킹한다.
# 다음 잡에서 재시도 시 정상 동작하면 평소대로 채워진다.
_BLOCKED_FAILURE_RATE = 0.5


class NaverBlogBlockedError(Exception):
    """본문 fetch 실패율이 임계치를 넘어 일시 차단으로 판단한 경우."""


def enqueue_blog_fetch_job(
    *,
    place_id: int,
    user_id: Optional[int],
    queue,
    db: Session,
) -> str:
    """InstagramCrawlJob(kind='naver_blog_fetch') 행을 만들고 RQ 큐에 enqueue한다.

    잡 INSERT → db.commit() → queue.enqueue 순서를 지킨다 (워커가 행을 즉시
    조회해도 race가 안 생기도록). `crawl_instagram_async` 패턴 그대로.
    """
    job_id = str(uuid.uuid4())
    db.add(
        InstagramCrawlJob(
            id=job_id,
            kind="naver_blog_fetch",
            url="",  # NOT NULL이라 빈 문자열로 채움 — 이 잡에서는 url 의미 없음
            shortcode=None,
            status="pending",
            user_id=user_id,
            payload={"place_id": place_id},
        )
    )
    db.commit()

    queue.enqueue(
        "app.services.instagram_jobs.process_blog_fetch_job",
        job_id,
        job_id=job_id,
        job_timeout=120,
    )
    return job_id


def _should_refresh(place_id: int, db: Session) -> bool:
    """최신 place_raw_data(provider='naver_blog')가 30일 이내면 False."""
    threshold = datetime.now(timezone.utc) - timedelta(days=_REFRESH_DAYS)
    fresh = (
        db.query(PlaceRawData.id)
        .filter(
            PlaceRawData.place_id == place_id,
            PlaceRawData.provider == "naver_blog",
            PlaceRawData.collected_at >= threshold,
        )
        .first()
    )
    return fresh is None


# 한국 행정구역 시/도 접미사 — 검색어에 들어가면 매칭 정확도가 떨어져 제거한다.
_PROVINCE_SUFFIXES = ("특별시", "광역시", "특별자치시", "특별자치도", "도")


def _build_query(place: Place) -> str:
    """place.name + 주소에서 시/도 접미사를 뺀 첫 두 토큰을 붙여 쿼리 문자열로.

    예: name="불란서와이너리 영등포점", address="서울특별시 영등포구 영등포동..." →
        쿼리 "불란서와이너리 영등포점 영등포구 영등포동" (시/도 토큰 빠짐).
    """
    name = (place.name or "").strip()
    if not name:
        return ""
    tokens = (place.address or "").split()
    admin = [t for t in tokens if not t.endswith(_PROVINCE_SUFFIXES)][:2]
    if not admin:
        return name
    return f"{name} {' '.join(admin)}".strip()


def _is_naver_blog_quota_exceeded(db: Session) -> bool:
    """이번 달 실제 API 호출이 발생한 잡 수가 NAVER_BLOG_MONTHLY_QUOTA_CALLS 한도를 넘었는지.

    `_should_refresh=False`로 스킵된 잡은 외부 API를 안 부르므로 카운트에서 빠진다.
    payload에 'inserted_reviews' 키가 있는 행만 실호출로 본다.
    """
    quota_str = os.getenv("NAVER_BLOG_MONTHLY_QUOTA_CALLS")
    if not quota_str:
        return False
    try:
        quota = int(quota_str)
    except ValueError:
        return False
    if quota <= 0:
        return True

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = (
        db.query(sa_func.count(InstagramCrawlJob.id))
        .filter(
            InstagramCrawlJob.kind == "naver_blog_fetch",
            InstagramCrawlJob.status == "done",
            InstagramCrawlJob.created_at >= month_start,
            InstagramCrawlJob.payload.has_key("inserted_reviews"),  # noqa: W601 — JSONB ? 연산자
        )
        .scalar()
        or 0
    )
    return count >= quota


def _max_chars() -> int:
    raw = os.getenv("NAVER_BLOG_BODY_MAX_CHARS")
    if not raw:
        return 2000
    try:
        v = int(raw)
        return v if v > 0 else 2000
    except ValueError:
        return 2000


def _fetch_sleep() -> float:
    raw = os.getenv("NAVER_BLOG_BODY_FETCH_SLEEP")
    if not raw:
        return 0.5
    try:
        v = float(raw)
        return v if v >= 0 else 0.5
    except ValueError:
        return 0.5


def fetch_and_persist_blog_reviews(place_id: int, db: Session) -> dict:
    """주 진입점: 네이버 블로그 검색 + 본문 fetch + DB 적재.

    반환:
      - {"skipped": "fresh"} — 30일 이내 데이터 존재 (외부 호출 0회)
      - {"inserted_reviews": N, "fetched_bodies": M, "failed_bodies": K,
         "skipped_short": L, "raw_data_id": id}
    예외:
      - NaverBlogSearchError — 검색 API 실패
      - NaverBlogBlockedError — 본문 fetch 실패율이 임계치 초과
    """
    if not _should_refresh(place_id, db):
        return {"skipped": "fresh"}

    place = db.query(Place).filter(Place.id == place_id).first()
    if place is None:
        # Place가 사라졌으면 의미 있는 작업 없음. 스킵 처리.
        return {"skipped": "place_not_found"}

    query = _build_query(place)
    if not query:
        return {"skipped": "empty_query"}

    items = naver_blog_search.search_blog_posts(query, display=10)

    max_chars = _max_chars()
    sleep_sec = _fetch_sleep()

    bodies: list[tuple[naver_blog_search.NaverBlogItem, Optional[str], dict]] = []
    fetched = 0
    failed = 0
    for idx, item in enumerate(items):
        body, meta = naver_blog_body_fetcher.fetch_blog_body(
            item.link, max_chars=max_chars
        )
        if body:
            fetched += 1
        else:
            failed += 1
        bodies.append((item, body, meta))
        # 마지막 호출 뒤에는 굳이 잘 필요 없음
        if idx < len(items) - 1 and sleep_sec > 0:
            time.sleep(sleep_sec)

    # 차단 감지: 본문이 한 건도 안 들어왔는데 검색 결과는 있었던 케이스.
    # items가 0이면 실패율 계산 의미 없음.
    if items and (failed / len(items)) >= _BLOCKED_FAILURE_RATE and fetched == 0:
        raise NaverBlogBlockedError(
            f"blocked: failure_rate={failed}/{len(items)}"
        )

    raw_payload = {
        "query": query,
        "display": 10,
        "sort": "sim",
        "fetched_bodies": fetched,
        "failed_bodies": failed,
        "items": [
            {
                "title": item.title,
                "link": item.link,
                "description": item.description,
                "bloggername": item.bloggername,
                "bloggerlink": item.bloggerlink,
                "postdate": item.postdate.isoformat() if item.postdate else None,
                "body": body,
                "body_meta": meta,
                "raw": item.raw,
            }
            for item, body, meta in bodies
        ],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    raw_row = PlaceRawData(
        place_id=place_id,
        provider="naver_blog",
        provider_place_id=None,  # naver_blog는 place 단위가 아니라 검색 결과 묶음
        raw_payload=raw_payload,
    )
    db.add(raw_row)
    db.flush()  # raw_row.id 확보

    inserted_reviews = 0
    skipped_short = 0
    for item, body, _meta in bodies:
        if not item.link:
            continue
        text = body if body else (item.description or "")
        if len(text) < 300:
            skipped_short += 1
        # ON CONFLICT DO NOTHING — 같은 link가 다른 잡에서 들어오면 무시
        stmt = (
            pg_insert(PlaceReview)
            .values(
                place_id=place_id,
                raw_data_id=raw_row.id,
                provider="naver_blog",
                external_review_id=item.link,
                rating=None,
                text=text,
                reviewed_at=item.postdate,
            )
            .on_conflict_do_nothing(
                # PlaceReview의 unique 인덱스가 partial(external_review_id IS NOT NULL)이라
                # index_where를 명시해야 conflict target 추론이 성공한다.
                index_elements=["place_id", "provider", "external_review_id"],
                index_where=sa_text("external_review_id IS NOT NULL"),
            )
        )
        result = db.execute(stmt)
        # rowcount가 1이면 INSERT, 0이면 충돌로 스킵
        if result.rowcount and result.rowcount > 0:
            inserted_reviews += 1

    db.commit()

    return {
        "inserted_reviews": inserted_reviews,
        "fetched_bodies": fetched,
        "failed_bodies": failed,
        "skipped_short": skipped_short,
        "raw_data_id": raw_row.id,
    }
