"""유저 공간 DNA 자동 갱신.

스팟 방문 체크인(`is_visited` 변화) 또는 visited 스팟 소프트 삭제가 발생하면
사용자가 added_by인 visited 스팟 전체의 PlaceSpaceDNA를 모아 평균을 다시 계산한다.
unvisit·삭제로 인한 드리프트를 막기 위해 incremental 누적 대신 매번 rebuild.

옵션 2a (2026-05-14): 첫 rebuild 시점(`total_visits==0`)에는 user_space_dna에
이미 들어있는 온보딩 값을 평균 풀에 1건 추가한다. 온보딩은 콜드 스타트 보조 신호로
첫 valid 방문 1회에만 묻혀들어가고, 그 다음 rebuild부터는 PlaceSpaceDNA만으로
순수 평균. 영구 보존이 필요하면 옵션 1(별도 컬럼 + 마이그레이션)로 확장.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import PlaceSpaceDNA, Spot, UserSpaceDNA, UserSpaceDNAHistory
from app.schemas.dna import AXIS_TYPES

logger = logging.getLogger(__name__)

def _is_valid_axes(axes: dict | None) -> bool:
    """AI 분석 결과 mbti_axes가 평균 계산에 사용 가능한지 검사.

    AI팀 스킴 변경에 강건하도록 키 셋 강제는 하지 않고 "비어있지 않은 dict이며
    모든 값이 number"인지만 본다. 평균은 모든 valid row에서 공통으로 등장한
    키만 사용(`rebuild_user_dna` 참고).
    """
    if not isinstance(axes, dict) or not axes:
        return False
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        for v in axes.values()
    )


def _average_axes(valid: list[dict]) -> dict:
    """평균 풀의 valid axes 리스트를 받아 평균 dict 반환.

    모든 row에 공통으로 존재하는 키만 평균에 사용 (AI 스킴 변경기 안전망).
    빈 리스트면 빈 dict.
    """
    if not valid:
        return {}
    common_keys = set(valid[0].keys())
    for a in valid[1:]:
        common_keys &= set(a.keys())
    n = len(valid)
    return {k: sum(a[k] for a in valid) / n for k in common_keys}


def _flatten_onboarding_axes(axes: dict | None) -> dict | None:
    """온보딩의 중첩 dict `{axis: {type_a, type_b}}`를 AI 응답과 동일한 단일 값
    형태 `{axis: type_a 비율}`로 평탄화해 평균 풀에 같이 넣을 수 있게 한다.

    AI 단일 값은 AXIS_TYPES 첫 요소(예: color.high) 비율 의미라는 AI팀 확인을
    따라, 온보딩 dict에서도 동일 위치(첫 유형) 값을 추출한다. 이미 단일 값
    형태면 그대로 반환.
    """
    if not isinstance(axes, dict) or not axes:
        return None
    result: dict = {}
    for axis, val in axes.items():
        if axis not in AXIS_TYPES:
            # 미지의 축은 단일 값일 때만 통과 (스킴 변화 호환).
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                result[axis] = float(val)
            continue
        type_a, _ = AXIS_TYPES[axis]
        if isinstance(val, dict):
            type_a_val = val.get(type_a)
            if isinstance(type_a_val, (int, float)) and not isinstance(type_a_val, bool):
                result[axis] = float(type_a_val)
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            result[axis] = float(val)
    return result or None


def rebuild_user_dna(user_id: int, db: Session) -> int:
    """사용자의 visited 스팟 + PlaceSpaceDNA를 다시 평균 내 user_space_dna에 upsert.

    옵션 2a: 첫 rebuild(`total_visits==0`)에서 user_space_dna에 이미 들어있는
    온보딩 값(중첩 dict)을 평탄화해 평균 풀에 spot DNA와 함께 1건 추가한다.
    두 번째 rebuild부터는 자연스럽게 spot DNA만 평균. 온보딩은 visit이 아니므로
    `total_visits` 카운트에는 포함하지 않는다.

    반환: 평균 계산에 합산된 visited spot 수 (관측용, 온보딩 row 제외).
    """
    start = time.perf_counter()

    existing = db.get(UserSpaceDNA, user_id)
    is_first_rebuild = existing is not None and existing.total_visits == 0

    rows = (
        db.query(PlaceSpaceDNA.mbti_axes)
        .join(Spot, Spot.place_id == PlaceSpaceDNA.place_id)
        .filter(
            Spot.added_by == user_id,
            Spot.is_visited.is_(True),
            Spot.deleted_at.is_(None),
        )
        .all()
    )

    valid = [axes for (axes,) in rows if _is_valid_axes(axes)]
    n_spots = len(valid)

    onboarding_blended = False
    if is_first_rebuild:
        flattened = _flatten_onboarding_axes(existing.mbti_axes)
        if flattened is not None and _is_valid_axes(flattened):
            valid.append(flattened)
            onboarding_blended = True

    new_axes = _average_axes(valid)

    now = datetime.now(timezone.utc)
    stmt = pg_insert(UserSpaceDNA).values(
        user_id=user_id,
        mbti_axes=new_axes,
        total_visits=n_spots,
        last_analyzed=now,
    ).on_conflict_do_update(
        index_elements=[UserSpaceDNA.user_id],
        set_={
            "mbti_axes": new_axes,
            "total_visits": n_spots,
            "last_analyzed": now,
        },
    )
    db.execute(stmt)
    db.commit()

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "user_dna.rebuild user_id=%d n_spots=%d onboarding_blended=%s elapsed_ms=%d",
        user_id,
        n_spots,
        onboarding_blended,
        elapsed_ms,
    )
    return n_spots


def record_history_for_spot(user_id: int, spot_id: int, db: Session) -> None:
    """이 spot의 기여 시점 user mbti_axes 스냅샷을 history에 upsert.

    `spot_id`는 unique 제약이 걸려 있어, 같은 spot에 대한 두 번째 트리거는 update.
    """
    user_dna = db.get(UserSpaceDNA, user_id)
    snapshot = user_dna.mbti_axes if user_dna else {}

    stmt = pg_insert(UserSpaceDNAHistory).values(
        user_id=user_id,
        spot_id=spot_id,
        mbti_axes_snapshot=snapshot,
    ).on_conflict_do_update(
        index_elements=[UserSpaceDNAHistory.spot_id],
        set_={
            "mbti_axes_snapshot": snapshot,
            "created_at": func.now(),
            "user_id": user_id,
        },
    )
    db.execute(stmt)
    db.commit()


def update_user_dna_after_spot_change(user_id: int, spot_id: int) -> None:
    """BackgroundTasks 진입점. 새 DB 세션을 열어 rebuild + history 갱신."""
    db = SessionLocal()
    try:
        rebuild_user_dna(user_id, db)
        record_history_for_spot(user_id, spot_id, db)
    except Exception:
        logger.exception(
            "user_dna.update failed user_id=%d spot_id=%d", user_id, spot_id
        )
    finally:
        db.close()
