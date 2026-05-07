import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.models import PlaceReview

NAVER_BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"

# 일반 브라우저로 위장하지 않으면 네이버 블로그가 빈 페이지를 반환하는 경우 있음
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

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

# 네이버 블로그 본문 셀렉터 (Smart Editor 3 → 구버전 순)
_NAVER_BODY_SELECTORS = [
    "div.se-main-container",
    "#postViewArea",
    "#post-area",
    "div.post_ct",
]

# 그 외 블로그 플랫폼(티스토리, 브런치, 워드프레스 등) 일반 셀렉터
_GENERIC_BODY_SELECTORS = [
    ".tt_article_useless_p_margin",  # Tistory
    ".entry-content",
    ".wrap_body",  # Brunch
    "article",
    "main",
]

# 본문 텍스트 최대 길이 (DB 부하 및 토큰 비용 방어)
_MAX_BODY_LENGTH = 10000


def _strip_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    cleaned = _HTML_TAG_RE.sub("", text)
    for entity, char in _HTML_ENTITIES.items():
        cleaned = cleaned.replace(entity, char)
    return cleaned.strip()


def _parse_postdate(postdate: Optional[str]) -> Optional[datetime]:
    """네이버 블로그 API postdate(YYYYMMDD)를 datetime으로 변환."""
    if not postdate or len(postdate) != 8:
        return None
    try:
        return datetime.strptime(postdate, "%Y%m%d")
    except ValueError:
        return None


def _normalize_naver_blog_url(url: str) -> str:
    """
    네이버 블로그 URL을 모바일 버전으로 변환.

    데스크톱 URL은 frameset이라 본문이 iframe 안에 있어 직접 파싱이 어렵지만,
    모바일 URL(m.blog.naver.com)은 본문이 HTML에 직접 렌더링되어 추출이 쉽다.
    """
    if "blog.naver.com" not in url or "m.blog.naver.com" in url:
        return url
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc="m.blog.naver.com"))


def _extract_body_text(html: str, url: str) -> Optional[str]:
    """HTML에서 본문 텍스트만 추출. 셀렉터 매칭 실패 시 None."""
    soup = BeautifulSoup(html, "html.parser")

    # script/style 요소는 제거 (텍스트 노이즈 방지)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # 네이버 블로그 우선
    if "naver.com" in url:
        for selector in _NAVER_BODY_SELECTORS:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(separator="\n", strip=True)
                if text:
                    return text

    # 일반 블로그 셀렉터 fallback
    for selector in _GENERIC_BODY_SELECTORS:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(separator="\n", strip=True)
            if text:
                return text

    # 최후의 수단: og:description (대부분 요약문)
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        return og_desc["content"].strip()

    return None


def _fetch_blog_body(url: str, timeout: float = 10.0) -> Optional[str]:
    """블로그 URL에서 본문 텍스트를 추출. 실패 시 None."""
    fetch_url = _normalize_naver_blog_url(url)
    try:
        resp = httpx.get(
            fetch_url,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
        )
    except httpx.RequestError:
        return None

    if resp.status_code != 200:
        return None

    body = _extract_body_text(resp.text, fetch_url)
    if body and len(body) > _MAX_BODY_LENGTH:
        body = body[:_MAX_BODY_LENGTH]
    return body


def _search_blog_posts(query: str, display: int = 10) -> list[dict]:
    """네이버 블로그 검색 API 호출. 실패 시 빈 리스트."""
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return []

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": display, "sort": "sim"}

    try:
        resp = httpx.get(NAVER_BLOG_SEARCH_URL, headers=headers, params=params, timeout=5.0)
    except httpx.RequestError:
        return []

    if resp.status_code != 200:
        return []

    data = resp.json()
    return data.get("items") or []


def collect_reviews_for_place(
    place_id: int,
    query: str,
    raw_data_id: Optional[int] = None,
    display: int = 10,
) -> None:
    """
    장소에 대한 네이버 블로그 글을 검색해 본문을 추출하고 PlaceReview에 저장.

    - 동일 place_id에 provider="naver_blog" 리뷰가 이미 있으면 종료 (1회 수집 정책).
    - 본문 크롤링 성공 시: 본문을 text로 저장.
    - 본문 크롤링 실패 시: API 스니펫(title + description)을 fallback으로 저장.
    - (place_id, provider, external_review_id) unique index로 중복 자동 방지.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(PlaceReview.id)
            .filter(
                PlaceReview.place_id == place_id,
                PlaceReview.provider == "naver_blog",
            )
            .first()
        )
        if existing:
            return

        items = _search_blog_posts(query, display=display)
        if not items:
            return

        for item in items:
            link = item.get("link")
            if not link:
                continue

            title = _strip_html(item.get("title")) or ""
            description = _strip_html(item.get("description")) or ""

            body = _fetch_blog_body(link)
            if body:
                text = f"{title}\n\n{body}".strip() if title else body
            else:
                # 본문 추출 실패 → 스니펫이라도 저장 (AI팀이 길이로 필터 가능)
                snippet = f"{title}\n\n{description}".strip()
                text = snippet or None

            review = PlaceReview(
                place_id=place_id,
                raw_data_id=raw_data_id,
                provider="naver_blog",
                external_review_id=link,
                rating=None,
                text=text,
                reviewed_at=_parse_postdate(item.get("postdate")),
            )
            db.add(review)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
    finally:
        db.close()
