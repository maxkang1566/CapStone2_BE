"""네이버 Blog Search Open API 래퍼.

엔드포인트: https://openapi.naver.com/v1/search/blog.json
- 인증 헤더: X-Naver-Client-Id, X-Naver-Client-Secret (Local Search와 공유)
- 응답 items[*] 주요 필드:
    title       (HTML 태그 <b>...</b> 포함 가능, 검색어 강조)
    link        (블로그 포스트 URL — external_review_id로 사용)
    description (본문 스니펫 ~200자, HTML 태그 포함 가능)
    bloggername / bloggerlink
    postdate    (YYYYMMDD 문자열)

이 모듈은 Local Search 래퍼와 동일한 패턴(헤더·예외·HTML strip)을 따른다.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Optional

import httpx
from pydantic import BaseModel, Field

NAVER_BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"

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


class NaverBlogSearchError(Exception):
    """네이버 Blog Search 호출 중 발생하는 일반 예외."""


class NaverBlogItem(BaseModel):
    """정규화된 네이버 Blog Search 결과 1건."""
    title: str = Field(..., description="블로그 포스트 제목 (HTML stripped)")
    link: str = Field(..., description="블로그 포스트 URL — external_review_id로 사용")
    description: str = Field("", description="본문 스니펫 (HTML stripped) — 본문 fetch 실패 시 fallback")
    bloggername: Optional[str] = None
    bloggerlink: Optional[str] = None
    postdate: Optional[datetime] = Field(None, description="postdate(YYYYMMDD) 파싱")
    raw: dict = Field(default_factory=dict, description="원본 item 페이로드")


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    cleaned = _HTML_TAG_RE.sub("", text)
    for entity, char in _HTML_ENTITIES.items():
        cleaned = cleaned.replace(entity, char)
    return cleaned.strip()


def _parse_postdate(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d")
    except (ValueError, TypeError):
        return None


def _normalize_item(item: dict) -> NaverBlogItem:
    return NaverBlogItem(
        title=_strip_html(item.get("title")) or "",
        link=item.get("link") or "",
        description=_strip_html(item.get("description")) or "",
        bloggername=item.get("bloggername") or None,
        bloggerlink=item.get("bloggerlink") or None,
        postdate=_parse_postdate(item.get("postdate")),
        raw=item,
    )


def search_blog_posts(
    query: str,
    *,
    display: int = 10,
    sort: str = "sim",
    timeout: float = 5.0,
) -> list[NaverBlogItem]:
    """네이버 Blog Search로 블로그 포스트를 검색하고 정규화된 리스트를 반환한다.

    빈 query는 빈 리스트로 허용. 그 외 실패는 `NaverBlogSearchError`로 올린다.
    `display` 기본 10. API 최대 100. 무료 한도 25,000/일 안에서 충분히 안전.
    `sort="sim"` 기본 — MBTI 분석에는 시기보다 관련성이 우선.
    """
    query = (query or "").strip()
    if not query:
        return []

    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise NaverBlogSearchError(
            "NAVER_CLIENT_ID/SECRET이 설정되지 않았습니다."
        )

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query": query,
        "display": max(1, min(display, 100)),
        "sort": sort if sort in ("sim", "date") else "sim",
    }

    try:
        resp = httpx.get(NAVER_BLOG_SEARCH_URL, headers=headers, params=params, timeout=timeout)
    except httpx.RequestError as e:
        logger.warning("네이버 Blog Search 네트워크 오류: %s", e)
        raise NaverBlogSearchError(f"네이버 Blog Search 네트워크 오류: {e}") from e

    if resp.status_code != 200:
        logger.warning(
            "네이버 Blog Search 비정상 응답 status=%s body=%s",
            resp.status_code,
            resp.text[:200],
        )
        raise NaverBlogSearchError(
            f"네이버 Blog Search 비정상 응답: status={resp.status_code}"
        )

    try:
        data = resp.json()
    except ValueError as e:
        logger.warning("네이버 Blog Search 응답 JSON 파싱 실패: %s", e)
        raise NaverBlogSearchError(f"네이버 Blog Search 응답 파싱 실패: {e}") from e

    items = data.get("items") or []
    return [_normalize_item(item) for item in items]
