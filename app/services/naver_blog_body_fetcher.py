"""네이버 모바일 블로그 페이지에서 본문을 직접 긁는 가벼운 fetcher.

데스크톱 `blog.naver.com`은 본문이 `PostView.naver` iframe 안이라 단순 fetch 불가.
`m.blog.naver.com`은 직접 HTML 렌더라 httpx + BeautifulSoup으로 충분.
Playwright 미사용 → 메모리·차단 위험 둘 다 회피.

학술 캡스톤 프로젝트의 분석용 데이터 수집 한정. 사용자에게 노출 안 함.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 모바일 위장 UA — 데스크톱 UA로 m.blog.naver.com에 접근하면 일부 페이지가 리다이렉트되거나
# 본문이 빈 채로 떨어진다.
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)

# 본문 셀렉터 우선순위.
# - SmartEditor 3 (현재 최신): div.se-main-container
# - 구버전: #postViewArea, div.post_ct
_BODY_SELECTORS: tuple[str, ...] = (
    "div.se-main-container",
    "#postViewArea",
    "div.post_ct",
)

# 광고/협찬 마커 — AI 분석 시 가중치 조정에 쓰도록 메타에만 기록한다.
_SPONSOR_MARKERS = ("협찬", "원고료", "체험단", "소정의")

_BLOG_NAVER_RE = re.compile(r"https?://blog\.naver\.com/(.+)$")
_M_BLOG_PREFIX = "https://m.blog.naver.com/"


def _to_mobile_url(link: str) -> str:
    """blog.naver.com → m.blog.naver.com 변환. 이미 모바일이면 그대로."""
    if not link:
        return link
    if link.startswith(_M_BLOG_PREFIX):
        return link
    m = _BLOG_NAVER_RE.match(link)
    if m:
        return _M_BLOG_PREFIX + m.group(1)
    return link


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch_blog_body(
    link: str,
    *,
    max_chars: int = 2000,
    timeout: float = 5.0,
) -> tuple[Optional[str], dict]:
    """모바일 블로그 페이지에서 본문을 추출한다.

    반환: (body_text or None, meta)
        body_text: 추출 성공 시 max_chars로 자른 본문 문자열, 실패 시 None
        meta: {"sponsored": bool, "selector_used": str | None}

    실패 케이스(모두 None 반환):
    - 네트워크/타임아웃
    - 비정상 HTTP 상태
    - 셀렉터 모두 miss
    - 추출된 본문이 빈 문자열
    """
    meta: dict = {"sponsored": False, "selector_used": None}
    if not link:
        return None, meta

    mobile_url = _to_mobile_url(link)
    headers = {
        "User-Agent": _MOBILE_USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        resp = httpx.get(mobile_url, headers=headers, timeout=timeout, follow_redirects=True)
    except httpx.RequestError as e:
        logger.debug("blog body fetch network error: %s url=%s", e, mobile_url)
        return None, meta

    if resp.status_code != 200:
        logger.debug("blog body fetch non-200: status=%s url=%s", resp.status_code, mobile_url)
        return None, meta

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:  # noqa: BLE001 — 어떤 파싱 오류도 None으로 흡수
        logger.debug("blog body parse error url=%s", mobile_url)
        return None, meta

    container = None
    selector_used: Optional[str] = None
    for selector in _BODY_SELECTORS:
        container = soup.select_one(selector)
        if container is not None:
            selector_used = selector
            break

    if container is None:
        return None, meta

    raw_text = container.get_text(separator=" ", strip=True)
    text = _normalize_whitespace(raw_text)
    if not text:
        return None, meta

    sponsored = any(marker in text for marker in _SPONSOR_MARKERS)
    truncated = text[:max_chars] if len(text) > max_chars else text

    meta["sponsored"] = sponsored
    meta["selector_used"] = selector_used
    return truncated, meta
