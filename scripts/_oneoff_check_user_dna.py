"""특정 user_id로 rebuild_user_dna 강제 실행 + before/after 비교.

사용:
    poetry run python scripts/_oneoff_check_user_dna.py --user-id 1
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.models.models import Spot, UserSpaceDNA, UserSpaceDNAHistory
from app.services.user_dna import rebuild_user_dna, record_history_for_spot


def _dump_dna(db, user_id: int, label: str) -> None:
    dna = db.get(UserSpaceDNA, user_id)
    history_count = (
        db.query(UserSpaceDNAHistory).filter(UserSpaceDNAHistory.user_id == user_id).count()
    )
    print(f"--- {label} (user_id={user_id}) ---")
    if dna is None:
        print("  user_space_dna: <없음>")
    else:
        print(
            f"  user_space_dna: total_visits={dna.total_visits} "
            f"last_analyzed={dna.last_analyzed.isoformat() if dna.last_analyzed else None}"
        )
        print(f"  mbti_axes: {json.dumps(dna.mbti_axes, ensure_ascii=False, indent=2)}")
    print(f"  history rows: {history_count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument(
        "--snapshot-spot-id",
        type=int,
        default=None,
        help="지정 시 record_history_for_spot 함께 호출 (해당 spot이 user 소유이고 visited 여야 의미 있음)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        _dump_dna(db, args.user_id, "BEFORE")

        visited_count = (
            db.query(Spot)
            .filter(
                Spot.added_by == args.user_id,
                Spot.visited_at.is_not(None),
                Spot.deleted_at.is_(None),
            )
            .count()
        )
        print(f"\nuser visited spots in DB: {visited_count}\n")

        n = rebuild_user_dna(args.user_id, db)
        print(f"rebuild_user_dna -> {n} spots aggregated\n")

        if args.snapshot_spot_id is not None:
            record_history_for_spot(args.user_id, args.snapshot_spot_id, db)
            print(f"record_history_for_spot(spot_id={args.snapshot_spot_id}) OK\n")

        _dump_dna(db, args.user_id, "AFTER")
    finally:
        db.close()


if __name__ == "__main__":
    main()
