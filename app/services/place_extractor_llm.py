"""LLM 기반 인스타 캡션 장소 추출기.

`place_extractor.py`(정규식)가 큐레이션 게시물에서 가게명을 못 잡는 한계 보완.
호출부(`instagram_share.share_post`)는 LLM 우선, None 반환 시 정규식 폴백.

폴백 보장(None 반환 조건):
- ANTHROPIC_API_KEY 미설정
- API 에러·타임아웃
- 파싱·예외

응답 스키마(`ExtractionResult.queries`)는 LLM이 직접 네이버 검색용 query 문자열로
구성한다. 후처리(name/address 결합) 없음. 환각·잡음은 호출부의 3중 방어로 거른다:
(1) `_NON_PLACE_CATEGORY_GROUPS` 카테고리 차단 (2) 네이버 검색 0건 자동 폐기
(3) 유니크 1건만 자동 저장.

도입 배경: notes/2026-05-14-place-extractor-llm.md.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

import anthropic
from anthropic import APIConnectionError, APIError
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 1024
_TIMEOUT_SEC = 15.0
_MAX_RETRIES = 2
_CAPTION_TRUNCATE = 2000


class ExtractionResult(BaseModel):
    """Claude 응답 강제 스키마."""

    queries: list[str] = Field(
        ...,
        description="네이버 검색용 query 문자열들. 가게 큐레이션이 아니면 빈 리스트.",
    )


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> Optional[anthropic.Anthropic]:
    """싱글톤 Anthropic 클라이언트. API 키 없으면 None."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — LLM 추출기 비활성")
        return None
    _client = anthropic.Anthropic(
        api_key=api_key,
        timeout=_TIMEOUT_SEC,
        max_retries=_MAX_RETRIES,
    )
    return _client


_SYSTEM_PROMPT = """\
당신은 인스타그램 캡션과 해시태그를 읽어 네이버 지도에서 검색할 가게(장소) query 문자열을 추출하는 분류기입니다.

작업:
- 캡션이 명시한 가게 모두를 query로 변환. 큐레이션 게시물(❶❷❸, 1./2./3. 등 번호 매김)도 모든 가게 추출.
- 각 query는 "가게명 + 명시된 행정구역 1개" 형태. 예: "티히커피 성북구", "어니언 안국점".
- 캡션에 행정구역 단서가 없으면 가게명만 단독 query.
- 체인점·프랜차이즈는 지점명을 분리하지 말고 그대로 포함. 예: "스타벅스 한성대입구역점" (지점명을 떼서 "스타벅스"만 쓰면 안 됨).

제외:
- 지하철역·버스정류장·기차역(예: "월곡역", "고려대역 6호선") — 가게 아님, query에서 제외.
- 행정구역 단독(예: "성북구", "이태원", "강남") — query에서 제외.
- 분위기·풍경·일상 묘사만 있고 가게명이 명시되지 않은 게시물 — queries=[].

환각 금지:
- 캡션·해시태그에 명시되지 않은 가게명을 생성하지 마세요.
- 가게명이 모호하거나 단서가 약하면 queries=[].

응답:
- queries: list[str] 한 필드만. 중복 없이, 캡션 등장 순서대로.
"""


def _build_user_message(caption: str, hashtags: Iterable[str]) -> str:
    tags = list(hashtags or [])
    tag_block = "\n".join(f"#{t}" for t in tags) if tags else "(없음)"
    return (
        f"## 캡션\n{caption[:_CAPTION_TRUNCATE]}\n\n"
        f"## 해시태그\n{tag_block}\n\n"
        "위 게시물이 가리키는 가게를 모두 식별해 네이버 검색용 query 리스트로 반환하세요."
    )


def _dedupe(queries: Iterable[str]) -> list[str]:
    """중복 제거(순서 보존). 공백·빈 문자열 스킵."""
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def extract_places(
    caption: str | None,
    *,
    hashtags: Iterable[str] = (),
) -> Optional[list[str]]:
    """캡션·해시태그에서 LLM으로 네이버 검색용 query 리스트를 추출.

    반환:
    - list[str]: query 리스트(중복 제거, 순서 보존). 빈 리스트는 "장소 게시물 아님" (LLM 단정).
    - None: API 키 미설정 / 입력 비어있음 / 예외 → 호출부가 정규식 폴백.
    """
    client = _get_client()
    if client is None:
        return None
    if not caption and not list(hashtags or ()):
        return None

    logger.debug("LLM 추출 시작 caption_len=%d", len(caption or ""))

    user_msg = _build_user_message(caption or "", hashtags)

    try:
        resp = client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_format=ExtractionResult,
        )
    except (APIError, APIConnectionError) as e:
        logger.warning("LLM 추출 API 호출 실패: %s", e)
        return None
    except Exception:
        logger.exception("LLM 추출 예외")
        return None

    result: Optional[ExtractionResult] = resp.parsed_output
    if result is None:
        logger.warning("LLM 추출 응답 파싱 실패")
        return None

    queries = _dedupe(result.queries)
    if not queries:
        logger.info("LLM 추출: 장소 게시물 아님으로 판단")
    else:
        logger.info("LLM 추출: %d queries", len(queries))
    return queries
