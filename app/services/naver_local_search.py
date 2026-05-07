"""네이버 Local Search Open API 래퍼.

엔드포인트: https://openapi.naver.com/v1/search/local.json
- 인증 헤더: X-Naver-Client-Id, X-Naver-Client-Secret
- 응답 items[*] 주요 필드:
    title       (HTML 태그 <b>...</b> 포함된 가게명)
    link        (가게의 외부 사이트 URL — 예약 시스템·홈페이지 등. **네이버 지도 ID가 아님**)
    category    ('음식점>한식>...' 형태 카테고리 트리)
    description (가게 설명, 비어있는 경우 다수)
    telephone   (전화번호, 비어있는 경우 다수)
    address     (지번 주소)
    roadAddress (도로명 주소)
    mapx, mapy  (WGS84 × 10^7 정수, 2018-12 이후. 별도 좌표 변환 불필요)

장소 검색 결과는 최대 5건 반환(API 제약).

**naver_place_id 처리**: API는 진짜 네이버 지도 place_id를 주지 않으므로
`name + roadAddress(또는 address)`를 정규화한 해시값을 ID로 사용한다. 같은 가게는
항상 같은 ID로 수렴하고, 다른 가게는 충돌 거의 0(SHA-1 16자).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Optional

import httpx
from pydantic import BaseModel, Field

NAVER_LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&nbsp;": " ",
}


class NaverLocalSearchError(Exception):
    """네이버 Local Search 호출 중 발생하는 일반 예외."""


class NaverLocalItem(BaseModel):
    """정규화된 네이버 Local Search 결과 1건."""
    naver_place_id: Optional[str] = Field(None, description="link URL에서 추출한 장소 ID")
    name: str
    address: Optional[str] = None
    road_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    category: Optional[str] = None
    category_group: Optional[str] = None
    phone: Optional[str] = None
    link: Optional[str] = None
    raw: dict = Field(default_factory=dict, description="원본 item 페이로드")


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    cleaned = _HTML_TAG_RE.sub("", text)
    for entity, char in _HTML_ENTITIES.items():
        cleaned = cleaned.replace(entity, char)
    return cleaned.strip()


def _make_place_id(name: str, address: Optional[str]) -> Optional[str]:
    """name + address 정규화 → SHA-1 16자 해시. name·address가 빈약하면 None."""
    name = (name or "").strip()
    address = (address or "").strip()
    if not name or not address:
        return None
    base = re.sub(r"\s+", " ", f"{name}|{address}".lower())
    return "naver_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _parse_coord(value: Optional[str]) -> Optional[float]:
    """mapx/mapy 정수 문자열을 위경도(float)로 변환. 2018-12 이후는 WGS84 × 10^7."""
    if value is None or value == "":
        return None
    try:
        return int(value) / 1e7
    except (ValueError, TypeError):
        return None


def _parse_category(category: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'음식점>한식>설렁탕' → (full='음식점>한식>설렁탕', group='음식점')."""
    if not category:
        return None, None
    head = category.split(">", 1)[0].strip() or None
    return category, head


def _normalize_item(item: dict) -> NaverLocalItem:
    title = _strip_html(item.get("title")) or ""
    link = item.get("link") or None
    full_category, category_group = _parse_category(item.get("category"))
    address = item.get("address") or None
    road_address = item.get("roadAddress") or None
    return NaverLocalItem(
        naver_place_id=_make_place_id(title, road_address or address),
        name=title,
        address=address,
        road_address=road_address,
        latitude=_parse_coord(item.get("mapy")),  # 위도
        longitude=_parse_coord(item.get("mapx")),  # 경도
        category=full_category,
        category_group=category_group,
        phone=item.get("telephone") or None,
        link=link,
        raw=item,
    )


def search_places(query: str, *, display: int = 5, timeout: float = 5.0) -> list[NaverLocalItem]:
    """네이버 Local Search로 장소를 검색하고 정규화된 리스트를 반환한다.

    빈 query는 빈 리스트로 허용(추출기가 빈 후보를 넣을 수 있음).
    그 외 실패 — 키 미설정·네트워크 오류·non-200·JSON 파싱 실패 — 는
    `NaverLocalSearchError`로 올려 상위에서 명시적으로 거절(5xx)하도록 한다.

    이 정책의 이유: `/instagram/share`는 후보 검색 결과의 카디널리티(0/1/N)로
    자동 저장 분기를 결정한다. 일부 후보만 성공한 결과로 분기하면 "보지 못한
    후보의 매칭"을 빠뜨려 잘못된 가게에 자동 저장되거나, "장소 게시물 아님"으로
    오판될 수 있다. 외부 의존성 실패를 빈 결과로 위장하지 않는다.
    """
    query = (query or "").strip()
    if not query:
        return []

    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise NaverLocalSearchError(
            "NAVER_CLIENT_ID/SECRET이 설정되지 않았습니다."
        )

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    # display 최대값은 API 정책상 5
    params = {"query": query, "display": max(1, min(display, 5)), "sort": "random"}

    try:
        resp = httpx.get(NAVER_LOCAL_SEARCH_URL, headers=headers, params=params, timeout=timeout)
    except httpx.RequestError as e:
        logger.warning("네이버 Local Search 네트워크 오류: %s", e)
        raise NaverLocalSearchError(f"네이버 Local Search 네트워크 오류: {e}") from e

    if resp.status_code != 200:
        logger.warning(
            "네이버 Local Search 비정상 응답 status=%s body=%s",
            resp.status_code,
            resp.text[:200],
        )
        raise NaverLocalSearchError(
            f"네이버 Local Search 비정상 응답: status={resp.status_code}"
        )

    try:
        data = resp.json()
    except ValueError as e:
        logger.warning("네이버 Local Search 응답 JSON 파싱 실패: %s", e)
        raise NaverLocalSearchError(f"네이버 Local Search 응답 파싱 실패: {e}") from e

    items = data.get("items") or []
    return [_normalize_item(item) for item in items]
