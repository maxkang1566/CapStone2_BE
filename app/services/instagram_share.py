"""인스타그램 자동 매핑 + 저장 오케스트레이션.

흐름:
1. 같은 storage에 동일 instagram_url Spot이 이미 있으면 즉시 already_saved 반환 (단축 경로)
2. instagram_pipeline.fetch_post → 캡션·이미지 확보 (캐시 hit이면 외부 호출 0회)
3. place_extractor.extract_candidates → 후보 텍스트 리스트
4. 각 후보당 naver_local_search.search_places → **첫 결과(1순위)만 채택** → naver_place_id 기준 dedupe
   (네이버 Local Search는 정렬 우선순위가 의미 있어, 1순위 외 결과를 다 모으면 dedup 후 다중이 되어
    `needs_selection`으로 떨어지는 비율이 비현실적으로 높았다 — 2026-05-09 dry-run 분석 결과)
5. 분기:
   - 유니크 후보 1개 → 자동 저장(spot_creator) → status="saved"
   - 유니크 후보 0개 → 장소 게시물이 아님 → status="not_a_place_post" (저장 안 함)
   - 유니크 후보 2개 이상 → 사용자 선택 필요 → status="needs_selection"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from sqlalchemy.orm import Session

from app.models.models import Spot, User
from app.schemas.instagram import (
    InstagramCrawlData,
    InstagramCrawlResponse,
    InstagramShareResponse,
    PlaceCandidate,
)
from app.schemas.spot import SpotResponse
from app.services import (
    instagram_pipeline,
    naver_local_search,
    place_disambiguator,
    place_extractor,
    place_extractor_llm,
)
from app.services.naver_local_search import NaverLocalItem
from app.services.playwright_manager import PlaywrightManager
from app.services.spot_creator import (
    InstagramData,
    NaverPlaceData,
    SpotCreateResult,
    create_spot_from_naver,
)


@dataclass
class ShareResult:
    status: Literal["saved", "needs_selection", "not_a_place_post"]
    spot: Optional[Spot] = None
    place_created: bool = False
    already_saved: bool = False
    crawl: Optional[InstagramCrawlResponse] = None
    crawl_source: Optional[str] = None  # 'apify' | 'cache_apify' | 'og_fallback' | 'cache_og_fallback'
    candidates: list[NaverLocalItem] = field(default_factory=list)


# 자동 매핑 후보로 부적합한 네이버 카테고리 그룹.
# 캡션의 행정구역명("이태원", "마곡") 검색 시 그 지역 구청·주민센터·관광명소가 1순위를
# 점령해 매핑 정확도를 떨어뜨리는 패턴이 잦아 자동 매핑 단계에서 제외한다.
# 단, candidates 응답에는 노출되므로(블랙리스트 외 결과만 제외) 사용자 수동 선택 흐름에는 영향 없음.
_NON_PLACE_CATEGORY_GROUPS = frozenset({
    "공공,사회기관",
    "행정복지센터",
    "특별,광역시청",
    "여행,명소",
    "교육,학문",
    "건강,의료",
    # 큐레이션 게시물에서 가게가 아니라 인근 지하철역(예: "고려대역 6호선")이 1순위로
    # 잡혀 들어오는 패턴이 잦아 차단. 2026-05-14 DWtRCqpkZPt(성북구 카페 큐레이션) 진단.
    "교통,운수",
})


def _is_place_business(item: NaverLocalItem) -> bool:
    """음식점·술집·카페 등 가게 카테고리이면 True. 행정기관·관광명소 류는 False."""
    return (item.category_group or "") not in _NON_PLACE_CATEGORY_GROUPS


def _dedupe_by_place_id(items: list[NaverLocalItem]) -> list[NaverLocalItem]:
    """naver_place_id 기준으로 첫 등장만 살린다. id 없는 항목은 제외(매핑 불가)."""
    seen: set[str] = set()
    out: list[NaverLocalItem] = []
    for item in items:
        if not item.naver_place_id:
            continue
        if item.naver_place_id in seen:
            continue
        seen.add(item.naver_place_id)
        out.append(item)
    return out


def _existing_spot_for_url(url: str, storage_id: int, db: Session) -> Optional[Spot]:
    return (
        db.query(Spot)
        .filter(
            Spot.storage_id == storage_id,
            Spot.instagram_url == url,
            Spot.deleted_at.is_(None),
        )
        .first()
    )


def _to_naver_place(item: NaverLocalItem) -> NaverPlaceData:
    return NaverPlaceData(
        naver_place_id=item.naver_place_id,
        name=item.name,
        address=item.road_address or item.address,
        latitude=item.latitude,
        longitude=item.longitude,
        category_group=item.category_group,
        phone=item.phone,
        raw_payload=item.raw,
    )


def _to_instagram_data(crawl: InstagramCrawlResponse) -> InstagramData:
    thumbnail = crawl.images[0] if crawl.images else None
    # 캐러셀 게시물은 crawl.images에 슬라이드 전체가 평탄 리스트로 들어온다(Apify 액터 응답).
    # 다중 이미지를 모두 spot_creator로 전달해 PlaceImage 다중 행 + Supabase 영구 저장이
    # 이뤄지게 한다. 단일 이미지 게시물이면 길이 1 리스트로 자연스럽게 동작.
    # shortcode를 채워 spot_creator가 이미 적재된 raw 행에 place_id를 UPDATE로 연결할 수 있게 한다.
    # share 흐름에서 fetch_post가 항상 먼저 호출되므로 raw는 이 시점에 존재함이 보장됨.
    return InstagramData(
        url=str(crawl.url),
        shortcode=instagram_pipeline.extract_shortcode(str(crawl.url)),
        caption=crawl.caption,
        thumbnail_url=thumbnail,
        image_urls=list(crawl.images) if crawl.images else None,
    )


def share_post(
    url: str,
    storage_id: int,
    current_user: User,
    db: Session,
    *,
    playwright_manager: Optional[PlaywrightManager] = None,
) -> ShareResult:
    """인스타 게시물 URL을 받아 자동 매핑이 가능하면 저장, 아니면 후보를 반환한다."""
    # 0) 이미 저장된 게시물이면 단축 경로
    existing = _existing_spot_for_url(url, storage_id, db)
    if existing:
        return ShareResult(
            status="saved",
            spot=existing,
            already_saved=True,
            place_created=False,
        )

    # 1) 캡션·이미지 확보
    crawl, source = instagram_pipeline.fetch_post(url, db, playwright_manager=playwright_manager)

    # 2) 캡션·해시태그에서 후보 추출 — LLM 우선, 실패 시 정규식 폴백.
    #    LLM은 큐레이션 게시물(N개 가게 묶음)에서 가게명을 직접 식별. 정규식은
    #    `📍`/`|` 마커 의존이라 큐레이션 포맷에서 가게명을 놓친다.
    candidate_texts = place_extractor_llm.extract_places(
        crawl.caption,
        hashtags=crawl.hashtags,
    )
    used_llm = candidate_texts is not None
    if not used_llm:
        candidate_texts = place_extractor.extract_candidates(
            crawl.caption,
            hashtags=crawl.hashtags,
        )

    # 3) 각 후보를 네이버 Local Search → 비-가게 카테고리(행정기관·관광명소·교육기관 등)를
    #    제외한 후 첫 결과(1순위) 1개만 채택해 합치기.
    #    - 1순위만 채택: 네이버 응답 정렬은 매칭 신뢰도 순이라 1순위만 봐도 정답일 가능성이 높다.
    #    - 비-가게 차단: 캡션의 행정구역명("이태원"·"마곡") 검색 시 그 지역 구청·주민센터·
    #      관광명소가 1순위를 점령해 정답을 밀어내는 패턴이 잦았다(2026-05-09 dry-run 분석).
    all_items: list[NaverLocalItem] = []
    for query in candidate_texts:
        results = naver_local_search.search_places(query)
        place_results = [r for r in results if _is_place_business(r)]
        if place_results:
            all_items.append(place_results[0])

    unique = _dedupe_by_place_id(all_items)

    # 4) 유니크 1개 → 자동 저장
    if len(unique) == 1:
        item = unique[0]
        spot_result: SpotCreateResult = create_spot_from_naver(
            _to_naver_place(item),
            _to_instagram_data(crawl),
            storage_id,
            current_user,
            db,
        )
        return ShareResult(
            status="saved",
            spot=spot_result.spot,
            place_created=spot_result.place_created,
            already_saved=spot_result.already_saved,
            crawl=crawl,
            crawl_source=source,
        )

    # 5) 유니크 0개 → 장소 게시물 아님 (저장 동작 없음)
    if len(unique) == 0:
        return ShareResult(
            status="not_a_place_post",
            crawl=crawl,
            crawl_source=source,
        )

    # 6) 유니크 2개 이상.
    #    LLM이 추출한 N개라면 큐레이션 게시물일 가능성이 높아 disambiguator skip하고
    #    바로 사용자 선택으로 보낸다. disambiguator는 "정답 1개" 모델이라 N개 모두가
    #    정답인 큐레이션에서 임의 1개를 골라 자동 저장하는 회귀를 만든다(2026-05-14 진단).
    #    정규식 폴백 경로(used_llm=False)는 dedupe가 약해 한 가게의 다른 표현이
    #    다중으로 살아남는 케이스가 많아 disambiguator의 1개 선택이 여전히 유효.
    if used_llm:
        return ShareResult(
            status="needs_selection",
            crawl=crawl,
            crawl_source=source,
            candidates=unique,
        )
    chosen = place_disambiguator.disambiguate(crawl.caption or "", unique)
    if chosen is not None:
        spot_result = create_spot_from_naver(
            _to_naver_place(chosen),
            _to_instagram_data(crawl),
            storage_id,
            current_user,
            db,
        )
        return ShareResult(
            status="saved",
            spot=spot_result.spot,
            place_created=spot_result.place_created,
            already_saved=spot_result.already_saved,
            crawl=crawl,
            crawl_source=source,
        )

    # 7) LLM도 정답 못 찾음 → 사용자 선택 필요
    return ShareResult(
        status="needs_selection",
        crawl=crawl,
        crawl_source=source,
        candidates=unique,
    )


def share_result_to_response(result: ShareResult) -> InstagramShareResponse:
    """ShareResult(서비스 dataclass) → InstagramShareResponse(API 스키마) 변환.

    라우터(동기 hit 분기)와 워커(비동기 done 분기) 둘 다 같은 매핑을 써야 하므로 분리.
    SpotResponse는 from_attributes=True라 ORM 객체에서 직접 검증한다 — 워커 측 호출은
    DB 세션이 살아 있는 트랜잭션 안에서 일어나야 함.
    """
    if result.status == "saved":
        spot_resp = SpotResponse.model_validate(result.spot) if result.spot else None
        return InstagramShareResponse(
            status="saved",
            spot=spot_resp,
            already_saved=result.already_saved,
            place_created=result.place_created,
            crawl_source=result.crawl_source,
        )

    crawl_data: Optional[InstagramCrawlData] = None
    if result.crawl is not None:
        thumbnail = result.crawl.images[0] if result.crawl.images else None
        # 캐러셀 전체 image_urls도 같이 노출 — needs_selection 분기에서 클라이언트가
        # /save로 candidates_context와 함께 되돌려보내야 다중 장소 이미지 분류가 동작한다.
        crawl_data = InstagramCrawlData(
            url=result.crawl.url,
            caption=result.crawl.caption,
            thumbnail_url=thumbnail,
            image_urls=list(result.crawl.images) if result.crawl.images else None,
        )

    if result.status == "not_a_place_post":
        return InstagramShareResponse(
            status="not_a_place_post",
            crawl_data=crawl_data,
            candidates=None,
            crawl_source=result.crawl_source,
        )

    candidates = [
        PlaceCandidate(
            naver_place_id=item.naver_place_id,
            name=item.name,
            address=item.address,
            road_address=item.road_address,
            latitude=item.latitude,
            longitude=item.longitude,
            category=item.category,
            category_group=item.category_group,
            phone=item.phone,
            link=item.link,
            raw_payload=item.raw,
        )
        for item in result.candidates
    ]
    return InstagramShareResponse(
        status="needs_selection",
        crawl_data=crawl_data,
        candidates=candidates,
        crawl_source=result.crawl_source,
    )
