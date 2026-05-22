"""캐러셀 이미지를 다중 후보 장소에 배정하는 분류기.

용도:
- 인스타 큐레이션 게시물(여러 장소를 한 게시물에 묶어 소개)은 캐러셀에 장소별
  사진이 섞여 있다. `/instagram/save`에서 사용자가 후보 중 1개를 선택해 저장할 때,
  다른 장소의 사진까지 함께 저장되지 않도록 시각+캡션 단서로 분류한다.

호출부 책임:
- 분류 결과(`{place_index: [image_index, ...]}`)에서 자기가 선택한 place_index에
  배정된 image_index만 추려 image_urls를 거른다.
- 0장이 배정된 경우의 대표 강제 폴백(`image_urls[0]` 추가)은 호출부가 결정한다.

폴백 정책(graceful degradation, 기존 LLM 모듈과 일치):
- ANTHROPIC_API_KEY 미설정 / API 실패 / 응답 검증 실패 / 이미지 다운로드 전건 실패
  → `{0: list(range(len(image_urls)))}` 반환(전체를 첫 후보에 몰아넣기).
- 일부 이미지만 다운로드 실패 → 모델은 다운로드 성공분만 분류, 실패분은 첫 후보에 묶어 보강.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Literal, Optional

import anthropic
import httpx
from anthropic import APIConnectionError, APIError
from PIL import Image
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 2048
_TIMEOUT_SEC = 30.0
_MAX_RETRIES = 2
# 같은 게시물·후보 조합에 대해 호출 간 분류 변동을 최소화하기 위한 명시적 0.
# Vision input은 완전 결정성을 보장하지 않으나 변동 폭이 크게 줄어든다.
# (관측: 1차 호출에서 슬라이드 1이 place 0, 2차 호출에서 같은 슬라이드가 place 1로
#  배정되어 동일 이미지가 두 Place에 동시에 들어가는 사례 — 2026-05-22 로컬 테스트)
_TEMPERATURE = 0.0
_CAPTION_TRUNCATE = 2000
_IMAGE_FETCH_TIMEOUT = 10.0
_IMAGE_MAX_DIM = 512
_IMAGE_JPEG_QUALITY = 85


class PlaceCandidateContext(BaseModel):
    """분류기에 전달되는 후보 장소 슬림 모델."""

    name: str
    category: Optional[str] = None


class ImageAssignment(BaseModel):
    """단일 이미지의 후보 장소 배정."""

    image_index: int = Field(..., description="입력 이미지 블록의 0-based 인덱스")
    place_index: int = Field(..., description="후보 장소의 0-based 인덱스")
    confidence: Literal["high", "medium", "low"]


class ImagePlaceMatchResult(BaseModel):
    """Claude 응답 강제 스키마."""

    assignments: list[ImageAssignment]


_client: Optional[anthropic.Anthropic] = None


def _get_client() -> Optional[anthropic.Anthropic]:
    """싱글톤 Anthropic 클라이언트. API 키 없으면 None."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — image_place_matcher 비활성")
        return None
    _client = anthropic.Anthropic(
        api_key=api_key,
        timeout=_TIMEOUT_SEC,
        max_retries=_MAX_RETRIES,
    )
    return _client


_SYSTEM_PROMPT = """\
당신은 인스타그램 큐레이션 게시물의 캐러셀 이미지를 캡션 안의 여러 장소 중 어느 곳에 해당하는지 분류하는 시각 분류기입니다.

작업:
- 입력된 각 이미지(image_index)를 후보 장소(place_index) 중 정확히 1개에 배정합니다.
- 시각 단서(간판/메뉴/외관/실내 분위기/조명 톤)와 캡션의 장소 섹션(번호·이모지·순서)을 종합 판단합니다.
- 시각 단서가 약하면 캡션 안 번호/순서를 신뢰합니다. 큐레이션 게시물은 보통 같은 장소의 사진들이 연속해 등장합니다.

confidence:
- "high" — 시각·캡션 단서가 모두 분명함
- "medium" — 한쪽 단서만 분명함
- "low" — 추정에 가깝지만 가장 가능성 있는 후보로 배정

응답:
- assignments: 입력 이미지와 동일한 길이. 각 image_index는 입력 범위 안에서 정확히 1번 등장. place_index는 후보 범위 안.
"""


def _prepare_image_b64(url: str) -> Optional[str]:
    """원격 이미지를 다운로드 → 512px max dim 리사이즈 → JPEG base64.

    분류용 임시본만 만들어 반환. DB 저장은 별도 경로(`image_storage`)에서 원본으로 처리.
    실패 시 None.
    """
    try:
        resp = httpx.get(url, timeout=_IMAGE_FETCH_TIMEOUT, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("matcher 이미지 fetch 실패: %s — %s", e.__class__.__name__, url[:80])
        return None
    if resp.status_code != 200 or not resp.content:
        logger.warning("matcher 이미지 fetch 비정상 응답 %s: %s", resp.status_code, url[:80])
        return None

    try:
        img = Image.open(io.BytesIO(resp.content))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((_IMAGE_MAX_DIM, _IMAGE_MAX_DIM), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_IMAGE_JPEG_QUALITY)
    except Exception as e:  # noqa: BLE001
        logger.warning("matcher 이미지 리사이즈 실패: %s — %s", e, url[:80])
        return None

    return base64.b64encode(buf.getvalue()).decode("ascii")


def _build_user_text(
    caption: str,
    candidates: list[PlaceCandidateContext],
    n_image_blocks: int,
) -> str:
    candidate_lines = []
    for i, c in enumerate(candidates):
        cat = f" ({c.category})" if c.category else ""
        candidate_lines.append(f"{i}. {c.name}{cat}")
    candidates_block = "\n".join(candidate_lines)
    return (
        f"## 캡션\n{caption[:_CAPTION_TRUNCATE]}\n\n"
        f"## 후보 장소 (place_index)\n{candidates_block}\n\n"
        f"## 이미지\n"
        f"위 메시지에 첨부된 이미지는 총 {n_image_blocks}장입니다. "
        f"각 이미지의 image_index는 첨부 순서대로 0부터 {n_image_blocks - 1}까지입니다.\n"
        f"각 이미지를 후보 장소(place_index) 중 정확히 1개에 배정해주세요."
    )


def _fallback_all_to_first(image_urls: list[str]) -> dict[int, list[int]]:
    """전체 이미지를 place_index=0에 몰아넣는 폴백. 빈 입력이면 빈 dict."""
    if not image_urls:
        return {}
    return {0: list(range(len(image_urls)))}


def match_images_to_places(
    caption: str,
    candidates: list[PlaceCandidateContext],
    image_urls: list[str],
) -> dict[int, list[int]]:
    """캡션·후보·이미지 URL 목록을 받아 {place_index: [image_index, ...]} 매핑 반환.

    실패 시 첫 후보에 전체 몰아넣기 폴백.
    """
    if not image_urls or not candidates:
        return _fallback_all_to_first(image_urls)

    client = _get_client()
    if client is None:
        return _fallback_all_to_first(image_urls)

    # 다운로드 + 리사이즈 + base64. 일부 실패는 valid_indices로 추적.
    image_blocks: list[dict] = []
    valid_indices: list[int] = []
    for idx, url in enumerate(image_urls):
        b64 = _prepare_image_b64(url)
        if b64 is None:
            continue
        image_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        })
        valid_indices.append(idx)

    if not image_blocks:
        logger.warning("matcher: 이미지 다운로드 전건 실패 — 폴백")
        return _fallback_all_to_first(image_urls)

    user_text = _build_user_text(caption or "", candidates, len(image_blocks))
    content: list[dict] = list(image_blocks)
    content.append({"type": "text", "text": user_text})

    try:
        resp = client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_format=ImagePlaceMatchResult,
        )
    except (APIError, APIConnectionError) as e:
        logger.warning("matcher API 호출 실패: %s", e)
        return _fallback_all_to_first(image_urls)
    except Exception:
        logger.exception("matcher 예외")
        return _fallback_all_to_first(image_urls)

    result: Optional[ImagePlaceMatchResult] = resp.parsed_output
    if result is None or not result.assignments:
        logger.warning("matcher 응답 파싱 실패 또는 빈 assignments — 폴백")
        return _fallback_all_to_first(image_urls)

    n_blocks = len(image_blocks)
    n_places = len(candidates)
    seen: set[int] = set()
    by_place: dict[int, list[int]] = {}

    for a in result.assignments:
        if not (0 <= a.image_index < n_blocks):
            logger.warning(
                "matcher: image_index=%s 범위 밖(0..%d) — 폴백", a.image_index, n_blocks - 1
            )
            return _fallback_all_to_first(image_urls)
        if a.image_index in seen:
            logger.warning("matcher: image_index=%s 중복 — 폴백", a.image_index)
            return _fallback_all_to_first(image_urls)
        if not (0 <= a.place_index < n_places):
            logger.warning(
                "matcher: place_index=%s 범위 밖(0..%d) — 폴백",
                a.place_index, n_places - 1,
            )
            return _fallback_all_to_first(image_urls)
        seen.add(a.image_index)
        # block_index → 원본 image_index 매핑
        original_image_index = valid_indices[a.image_index]
        by_place.setdefault(a.place_index, []).append(original_image_index)

    if len(seen) != n_blocks:
        logger.warning(
            "matcher: 일부 이미지 미배정(%d/%d) — 폴백", len(seen), n_blocks
        )
        return _fallback_all_to_first(image_urls)

    # 다운로드 실패한 이미지는 보수적으로 첫 후보에 묶기
    skipped = [i for i in range(len(image_urls)) if i not in set(valid_indices)]
    if skipped:
        logger.info("matcher: 다운로드 실패 %d장은 place_index=0에 묶음", len(skipped))
        by_place.setdefault(0, []).extend(skipped)

    for k in by_place:
        by_place[k] = sorted(by_place[k])

    logger.info(
        "matcher OK: %d이미지 → %d장소에 분배 (place별: %s)",
        len(image_urls), len(by_place),
        {k: len(v) for k, v in by_place.items()},
    )
    return by_place
