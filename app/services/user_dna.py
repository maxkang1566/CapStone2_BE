"""유저 공간 DNA 자동 갱신.

스팟 방문 체크인(`is_visited` 변화) 또는 visited 스팟 소프트 삭제가 발생하면
사용자가 added_by인 visited 스팟 전체의 PlaceSpaceDNA를 모아 평균을 다시 계산한다.
unvisit·삭제로 인한 드리프트를 막기 위해 incremental 누적 대신 매번 rebuild.
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

logger = logging.getLogger(__name__)

def _is_valid_axes(axes: dict | None) -> bool:
    """AI 분석 결과 mbti_axes가 평균 계산에 사용 가능한지 검사.

    AI팀 스킴 변경에 강건하도록 키 셋 강제는 하지 않고 "비어있지 않은 dict이며
    모든 값이 number"인지만 본다. 평균은 모든 valid row에서 공통으로 등장한
    키만 사용(`rebuild_user_dna` 참고).
    """
    if not isinstance(axes, dict) or not axes:
        return False
    return all(isinstance(v, (int, float)) for v in axes.values())


def rebuild_user_dna(user_id: int, db: Session) -> int:
    """사용자의 visited 스팟 + PlaceSpaceDNA를 다시 평균 내 user_space_dna에 upsert.

    반환: 평균 계산에 합산된 spot 수 (관측용).
    """
    start = time.perf_counter()

    rows = (
        db.query(PlaceSpaceDNA.mbti_axes)
        .join(Spot, Spot.place_id == PlaceSpaceDNA.place_id)
        .filter(
            Spot.added_by == user_id,
            Spot.visited_at.is_not(None),
            Spot.deleted_at.is_(None),
        )
        .all()
    )

    valid = [axes for (axes,) in rows if _is_valid_axes(axes)]
    n = len(valid)

    if n == 0:
        new_axes: dict = {}
    else:
        # 모든 valid row에 공통으로 존재하는 키만 평균 (AI 스킴 변경기 안전망).
        common_keys = set(valid[0].keys())
        for a in valid[1:]:
            common_keys &= set(a.keys())
        new_axes = {k: sum(a[k] for a in valid) / n for k in common_keys}

    now = datetime.now(timezone.utc)
    stmt = pg_insert(UserSpaceDNA).values(
        user_id=user_id,
        mbti_axes=new_axes,
        total_visits=n,
        last_analyzed=now,
    ).on_conflict_do_update(
        index_elements=[UserSpaceDNA.user_id],
        set_={
            "mbti_axes": new_axes,
            "total_visits": n,
            "last_analyzed": now,
        },
    )
    db.execute(stmt)
    db.commit()

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "user_dna.rebuild user_id=%d n_spots=%d elapsed_ms=%d",
        user_id,
        n,
        elapsed_ms,
    )
    return n


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
