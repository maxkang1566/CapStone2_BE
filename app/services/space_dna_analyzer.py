"""Place 저장 시 AI팀 외부 API 호출 → place_space_dna + place_tags 채움.

흐름:
    1. enqueue_space_dna_analysis(place_id, queue) — 라우터/워커가 RQ 잡 등록
    2. RQ 워커가 trigger_space_dna_analysis(place_id) 실행
       - 이미 valid한 분석이 있으면 skip(멱등). force=True면 가드 우회(백필 전용).
       - PlaceImage 다중 선택(최대 _MAX_IMAGES_PER_PLACE) → POST /analyze/multi
       - 응답 검증 후 place_space_dna upsert + place_tags 재구축

AI API 응답 스키마(2026-05-14 dry-run으로 확정, /analyze/multi도 동일):
    {
      "place_id": int,
      "dna_code": str,            # 예: "SMV"
      "mbti_axes": {key: float},  # 키 셋은 AI 알고리즘 버전에 따라 가변
      "top_tags": [{"tag_name": str, "score": float}, ...],
      "ai_summary": str,
      "updated_at": str
    }

AI API가 자체적으로 place_space_dna에 write하지만, 백엔드 upsert는 멱등 안전망
(외부 시스템과 무관하게 우리 DB 상태를 우리가 보장).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import httpx
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import PlaceImage, PlaceSpaceDNA, PlaceTag, Tag
from app.services.user_dna import _is_valid_axes

if TYPE_CHECKING:
    from rq import Queue

logger = logging.getLogger(__name__)

SPACE_DNA_API_URL = os.getenv(
    "SPACE_DNA_API_URL", "https://hoiiiii-dna-space.hf.space"
).rstrip("/")
# 다중 이미지 분석은 호출당 처리 시간이 이미지 수에 비례. 단일 이미지 ~8s 기준 10장
# 산술 80s + 모델 오버헤드라 180s 한도 안에서 안정 동작 기대. 측정 결과에 따라 조정.
SPACE_DNA_TIMEOUT_S = 180.0
# RQ job_timeout은 HTTP 타임아웃 + 여유 30s(응답 파싱·DB upsert 시간).
SPACE_DNA_JOB_TIMEOUT_S = 210

# 한 장소당 AI에 보낼 이미지 최대 개수.
# is_representative DESC, created_at ASC 정렬에서 상위 N장만 전송.
# 인스타 캐러셀 최대치(10)와 정합. 다중 장소 분류로 한 장소당 평균 1~3장이라 상한 도달은
# 사실상 드묾. 비용·지연·정확도 트레이드오프는 운영 로그 보고 조정.
_MAX_IMAGES_PER_PLACE = 10

# AI 응답의 태그명을 그대로 globally 노출되는 `tags` 마스터에 INSERT하기 때문에 최소 가드.
# v1 분석에서 부적절 태그가 관측되면 운영자가 이 set을 채운다.
TAG_BLOCKLIST: set[str] = set()
TAG_NAME_MIN = 1
TAG_NAME_MAX = 30


def enqueue_space_dna_analysis(
    place_id: int, queue: "Queue", *, force: bool = False
) -> None:
    """호출자(라우터/워커)가 RQ 잡으로 분석을 예약.

    force=True는 백필 전용 — `_already_analyzed` 가드를 우회해 강제 재분석.
    일상적 호출(인스타/네이버 저장 직후)은 기본 force=False로 멱등성 보호 유지.
    """
    queue.enqueue(
        "app.services.space_dna_analyzer.trigger_space_dna_analysis",
        place_id,
        kwargs={"force": force},
        job_timeout=SPACE_DNA_JOB_TIMEOUT_S,
    )


def trigger_space_dna_analysis(place_id: int, *, force: bool = False) -> None:
    """RQ 워커가 실행하는 분석 본체. 별도 SessionLocal로 외부 API 호출 + DB upsert.

    force=True면 `_already_analyzed` 가드를 우회 — AI 알고리즘 업데이트 반영용
    강제 재분석 백필에서만 사용. 라우터·워커 일반 호출 경로는 기본 force=False.
    """
    db = SessionLocal()
    try:
        if not force and _already_analyzed(place_id, db):
            logger.info("space_dna: skip place_id=%d already analyzed", place_id)
            return

        image_urls = _pick_image_urls(place_id, db)
        if not image_urls:
            logger.warning("space_dna: skip place_id=%d no PlaceImage", place_id)
            return

        logger.info(
            "space_dna: analyzing place_id=%d images=%d force=%s",
            place_id,
            len(image_urls),
            force,
        )

        with httpx.Client(timeout=SPACE_DNA_TIMEOUT_S) as client:
            resp = client.post(
                f"{SPACE_DNA_API_URL}/analyze/multi",
                json={"place_id": place_id, "image_urls": image_urls},
            )
            resp.raise_for_status()
            data = resp.json()

        mbti_axes = data.get("mbti_axes") or {}
        if not _is_valid_axes(mbti_axes):
            logger.error(
                "space_dna: invalid axes place_id=%d keys=%s",
                place_id,
                list(mbti_axes.keys()) if isinstance(mbti_axes, dict) else type(mbti_axes).__name__,
            )
            return

        now = datetime.now(timezone.utc)
        db.execute(
            pg_insert(PlaceSpaceDNA)
            .values(
                place_id=place_id,
                mbti_axes=mbti_axes,
                ai_summary=data.get("ai_summary"),
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[PlaceSpaceDNA.place_id],
                set_={
                    "mbti_axes": mbti_axes,
                    "ai_summary": data.get("ai_summary"),
                    "updated_at": now,
                },
            )
        )
        _rebuild_place_tags(place_id, data.get("top_tags") or [], db)
        db.commit()
        logger.info(
            "space_dna: upserted place_id=%d axes_keys=%s tags=%d",
            place_id,
            sorted(mbti_axes.keys()),
            len(data.get("top_tags") or []),
        )
    except Exception:
        logger.exception("space_dna: failed place_id=%d", place_id)
    finally:
        db.close()


def _already_analyzed(place_id: int, db: Session) -> bool:
    row = (
        db.query(PlaceSpaceDNA.mbti_axes)
        .filter(PlaceSpaceDNA.place_id == place_id)
        .first()
    )
    return bool(row) and _is_valid_axes(row[0])


def _pick_image_urls(place_id: int, db: Session) -> list[str]:
    """한 장소의 이미지 URL을 최대 _MAX_IMAGES_PER_PLACE개까지 반환.

    정렬: is_representative DESC (대표 이미지 먼저), created_at ASC (오래된 순).
    이미지 없으면 빈 리스트 반환.
    """
    rows = (
        db.query(PlaceImage.image_url)
        .filter(PlaceImage.place_id == place_id)
        .order_by(PlaceImage.is_representative.desc(), PlaceImage.created_at.asc())
        .limit(_MAX_IMAGES_PER_PLACE)
        .all()
    )
    return [r[0] for r in rows]


def _sanitize_tag_name(name: object) -> Optional[str]:
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not (TAG_NAME_MIN <= len(n) <= TAG_NAME_MAX):
        return None
    if n in TAG_BLOCKLIST:
        return None
    return n


def _rebuild_place_tags(place_id: int, top_tags: list[dict], db: Session) -> None:
    """top_tags → tags 마스터 upsert + place_tags 재구축.

    dry-run으로 확정된 AI 응답 키: {"tag_name": str, "score": float}.
    같은 잡 트랜잭션 내 DELETE + INSERT — 재분석 시 태그 변경 정합성 보장.
    """
    cleaned: list[tuple[str, Optional[float]]] = []
    for item in top_tags:
        if not isinstance(item, dict):
            continue
        name = _sanitize_tag_name(item.get("tag_name"))
        if name is None:
            continue
        raw_score = item.get("score")
        score = float(raw_score) if isinstance(raw_score, (int, float)) else None
        cleaned.append((name, score))

    if not cleaned:
        db.execute(delete(PlaceTag).where(PlaceTag.place_id == place_id))
        return

    for n, _ in cleaned:
        db.execute(
            pg_insert(Tag)
            .values(name=n)
            .on_conflict_do_nothing(index_elements=[Tag.name])
        )
    db.flush()

    tag_rows = (
        db.query(Tag.id, Tag.name)
        .filter(Tag.name.in_([n for n, _ in cleaned]))
        .all()
    )
    name_to_id = {n: i for i, n in tag_rows}

    db.execute(delete(PlaceTag).where(PlaceTag.place_id == place_id))
    for n, score in cleaned:
        tid = name_to_id.get(n)
        if tid is None:
            continue
        db.add(PlaceTag(place_id=place_id, tag_id=tid, score=score))
