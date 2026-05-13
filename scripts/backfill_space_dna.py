"""DNA 없는 Place 일괄 분석.

운영자가 PR 머지 직후 1회 실행해 시드 분량을 채우고, 이후엔 누락분 정기 처리.
재실행이 곧 resume — 쿼리(`LEFT JOIN ... WHERE psd.place_id IS NULL`)가
이미 처리된 행을 자동 제외한다.

사용:
    poetry run python scripts/backfill_space_dna.py            # 기본 20건
    poetry run python scripts/backfill_space_dna.py --limit 50
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.models.models import Place, PlaceSpaceDNA
from app.services.space_dna_analyzer import trigger_space_dna_analysis


def _list_missing(limit: int) -> list[int]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Place.id)
            .outerjoin(PlaceSpaceDNA, PlaceSpaceDNA.place_id == Place.id)
            .filter(PlaceSpaceDNA.place_id.is_(None))
            .order_by(Place.id.asc())
            .limit(limit)
            .all()
        )
        return [pid for (pid,) in rows]
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="DNA 없는 Place 일괄 분석")
    p.add_argument("--limit", type=int, default=20, help="이번 실행에서 처리할 최대 건수")
    args = p.parse_args()

    ids = _list_missing(args.limit)
    if not ids:
        print("backfill: DNA 없는 Place 없음 (모두 처리 완료)")
        return

    print(f"backfill: {len(ids)} place(s) to process (limit={args.limit})")
    t0 = time.perf_counter()
    for i, pid in enumerate(ids, 1):
        per_start = time.perf_counter()
        print(f"  [{i}/{len(ids)}] place_id={pid} ...", flush=True)
        trigger_space_dna_analysis(pid)
        per_elapsed = time.perf_counter() - per_start
        print(f"      done in {per_elapsed:.1f}s", flush=True)
    total = time.perf_counter() - t0
    print(f"backfill: done - {len(ids)} places / {total:.1f}s")


if __name__ == "__main__":
    main()
