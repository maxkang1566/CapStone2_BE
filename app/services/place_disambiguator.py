"""LLM 기반 장소 후보 disambiguator.

`instagram_share`에서 자동 매핑이 needs_selection으로 떨어지기 직전 호출한다.
캡션 + 네이버 후보 N개를 받아 정답 `naver_place_id` 1개 또는 None을 반환한다.

폴백 보장:
- ANTHROPIC_API_KEY 미설정·API 에러·타임아웃·환각(후보 외 ID)이면 None을 반환해
  호출부가 기존 needs_selection 흐름으로 안전하게 떨어진다.

환각 방지:
- 응답된 naver_place_id가 입력 후보 풀에 없으면 환각으로 간주, None 반환.

도입 배경: notes/2026-05-09 dry-run 분석에서 saved 비율이 옵션 C/E로도 16%에서
정체. needs_selection 후보 안에 정답이 들어 있는 케이스가 대부분이라
캡션 자연어 이해로 정답 1개를 고르는 단계가 필요했다.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import anthropic
from anthropic import APIError, APIConnectionError
from pydantic import BaseModel, Field

from app.services.naver_local_search import NaverLocalItem

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 256
_TIMEOUT_SEC = 15.0
_MAX_RETRIES = 2
_CAPTION_TRUNCATE = 2000


class DisambiguationResult(BaseModel):
    """Claude 응답 강제 스키마."""

    naver_place_id: Optional[str] = Field(
        None,
        description="후보 중 정답 가게의 naver_place_id. 단서가 약하거나 후보에 없으면 null.",
    )
    reason: str = Field(..., description="선택 또는 null 반환 근거 (한국어, 1-2문장)")


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> Optional[anthropic.Anthropic]:
    """싱글톤 Anthropic 클라이언트.

    ANTHROPIC_API_KEY가 없으면 None을 반환해 호출부가 폴백할 수 있게 한다.
    """
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — disambiguator 비활성")
        return None
    _client = anthropic.Anthropic(
        api_key=api_key,
        timeout=_TIMEOUT_SEC,
        max_retries=_MAX_RETRIES,
    )
    return _client


_SYSTEM_PROMPT = """\
당신은 인스타그램 게시물 캡션과 네이버 장소 검색 후보를 받아 정답 1개를 식별하는 분류기입니다.

판단 규칙:
- 캡션이 명확히 가리키는 가게가 후보 안에 있으면 그 naver_place_id를 반환
- 다음 중 하나라도 해당하면 naver_place_id는 null:
  * 캡션에 가게명이 명시되지 않음 (분위기·풍경 묘사만)
  * 후보 안에 정답이 없음
  * 후보 중 어느 것이 정답인지 단서가 부족해 모호함

판단 단서:
- 캡션의 가게명·지역(구·동·역명)·도로명 주소·메뉴
- 후보의 행정구·도로명·카테고리가 캡션 단서와 일치하는지
- 같은 브랜드 다른 지점이면 캡션의 지역 키워드와 일치하는 지점 선택

추측 금지: 단서가 약하면 null. 환각 금지: 후보에 없는 ID 만들지 마세요."""


def _format_candidates(candidates: list[NaverLocalItem]) -> str:
    lines: list[str] = []
    for idx, c in enumerate(candidates, 1):
        addr = c.road_address or c.address or "(주소 없음)"
        cat = c.category_group or c.category or "(카테고리 없음)"
        lines.append(
            f"{idx}. naver_place_id={c.naver_place_id} | {c.name} [{cat}] {addr}"
        )
    return "\n".join(lines)


def disambiguate(
    caption: str,
    candidates: list[NaverLocalItem],
) -> Optional[NaverLocalItem]:
    """캡션과 후보 N개를 받아 정답 1개 또는 None을 반환.

    None 반환 케이스:
    - API 키 미설정 / 빈 입력
    - LLM이 null 반환 (모호하다고 판단)
    - 응답 ID가 후보 풀에 없음 (환각)
    - 네트워크·API 예외

    호출부는 None이면 기존 needs_selection 흐름을 그대로 진행해야 한다.
    """
    client = _get_client()
    if client is None:
        return None
    if not caption or not candidates:
        return None

    valid_ids = {c.naver_place_id for c in candidates if c.naver_place_id}

    user_msg = (
        f"## 캡션\n{caption[:_CAPTION_TRUNCATE]}\n\n"
        f"## 후보 (총 {len(candidates)}개)\n{_format_candidates(candidates)}\n\n"
        "위 후보 중 캡션이 가리키는 가게의 naver_place_id 하나를 선택하세요. "
        "확신할 수 없으면 null."
    )

    try:
        resp = client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_format=DisambiguationResult,
        )
    except (APIError, APIConnectionError) as e:
        logger.warning("disambiguator API 호출 실패: %s", e)
        return None
    except Exception:
        logger.exception("disambiguator 예외")
        return None

    result: Optional[DisambiguationResult] = resp.parsed_output
    if result is None:
        logger.warning("disambiguator 응답 파싱 실패")
        return None
    if not result.naver_place_id:
        logger.info("disambiguator: NONE (이유=%s)", result.reason)
        return None
    if result.naver_place_id not in valid_ids:
        logger.warning(
            "disambiguator 환각 차단: %s (후보 외, 이유=%s)",
            result.naver_place_id, result.reason,
        )
        return None

    chosen = next(c for c in candidates if c.naver_place_id == result.naver_place_id)
    logger.info(
        "disambiguator: 선택 %s (%s) — %s",
        chosen.naver_place_id, chosen.name, result.reason,
    )
    return chosen
