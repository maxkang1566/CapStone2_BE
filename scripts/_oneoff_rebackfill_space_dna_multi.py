"""AI 알고리즘 업데이트 반영 — 전 Place 강제 재분석 백필 (1회성 oneoff).

배경:
    AI팀이 /analyze/multi 다중 이미지 분석으로 알고리즘을 업데이트했고, 백엔드도
    space_dna_analyzer가 이미지 1장 → N장 전송으로 전환됨(notes/2026-05-28).
    이미 분석된 Place는 단일 이미지 결과라 다중 이미지 입력으로 재분석할 필요가 있다.

    `trigger_space_dna_analysis`는 기본 `_already_analyzed` 가드로 skip되므로
    force=True 옵션을 명시해 가드를 우회한다(가드 자체는 일상 호출 경로 보호용으로 유지).

사용:
    # dry-run: 대상 place_id만 출력
    poetry run python scripts/_oneoff_rebackfill_space_dna_multi.py --dry-run

    # 표본 5건만 동기 처리(빠른 검증)
    poetry run python scripts/_oneoff_rebackfill_space_dna_multi.py --limit 5

    # 전체 동기 처리(운영 1회성)
    poetry run python scripts/_oneoff_rebackfill_space_dna_multi.py

    # 큐로 위임(Redis/RQ 워커 가동 시 권장 — 워커가 병렬 처리)
    poetry run python scripts/_oneoff_rebackfill_space_dna_multi.py --via-queue

대상 선정:
    PlaceImage가 1장 이상 있는 모든 Place. 이미지가 없는 Place는
    분석 자체가 불가하므로 자동 제외.

재진입 안전성:
    place_space_dna는 ON CONFLICT DO UPDATE로 upsert되고, place_tags는
    DELETE + INSERT로 재구축된다. 같은 place_id에 여러 번 호출돼도 마지막 결과로
    덮어쓰기.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import distinct

from app.core.database import SessionLocal
from app.models.models import PlaceImage
from app.services.space_dna_analyzer import (
    enqueue_space_dna_analysis,
    trigger_space_dna_analysis,
)


def _list_targets(limit: int | None) -> list[int]:
    """PlaceImage가 있는 모든 Place의 place_id 목록."""
    db = SessionLocal()
    try:
        q = (
            db.query(distinct(PlaceImage.place_id))
            .order_by(PlaceImage.place_id.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        return [pid for (pid,) in q.all()]
    finally:
        db.close()


def _enqueue_via_queue(place_ids: list[int]) -> None:
    """Redis/RQ가 가동 중일 때 큐로 위임. 워커가 병렬 처리."""
    import os
    import redis
    from rq import Queue

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    conn = redis.Redis.from_url(redis_url)
    q = Queue("default", connection=conn)

    for i, pid in enumerate(place_ids, 1):
        enqueue_space_dna_analysis(pid, q, force=True)
        print(f"  [{i}/{len(place_ids)}] enqueued place_id={pid}", flush=True)


def _run_sync(place_ids: list[int]) -> None:
    """동기 직접 호출. 워커 없이 한 번에 처리할 때."""
    t0 = time.perf_counter()
    for i, pid in enumerate(place_ids, 1):
        per_start = time.perf_counter()
        print(f"  [{i}/{len(place_ids)}] place_id={pid} ...", flush=True)
        trigger_space_dna_analysis(pid, force=True)
        per_elapsed = time.perf_counter() - per_start
        print(f"      done in {per_elapsed:.1f}s", flush=True)
    total = time.perf_counter() - t0
    print(f"\nrebackfill: done — {len(place_ids)} places / {total:.1f}s")


def main() -> None:
    p = argparse.ArgumentParser(description="AI 다중 이미지 알고리즘으로 전 Place 강제 재분석")
    p.add_argument("--limit", type=int, default=None, help="이번 실행 최대 건수(미지정 시 전체)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="대상 place_id만 출력하고 실제 호출은 안 함",
    )
    p.add_argument(
        "--via-queue",
        action="store_true",
        help="동기 호출 대신 RQ 큐에 enqueue (Redis + 워커 필요)",
    )
    args = p.parse_args()

    place_ids = _list_targets(args.limit)
    if not place_ids:
        print("rebackfill: PlaceImage가 있는 Place 없음")
        return

    print(
        f"rebackfill: {len(place_ids)} place(s) targeted "
        f"(limit={args.limit}, dry_run={args.dry_run}, via_queue={args.via_queue})"
    )
    if args.dry_run:
        for pid in place_ids:
            print(f"  place_id={pid}")
        print(f"\n(dry-run) {len(place_ids)} place(s) would be re-analyzed")
        return

    if args.via_queue:
        _enqueue_via_queue(place_ids)
        print(f"\nrebackfill: enqueued {len(place_ids)} place(s) — watch RQ worker logs")
    else:
        _run_sync(place_ids)


if __name__ == "__main__":
    main()
