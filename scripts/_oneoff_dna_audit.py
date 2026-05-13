"""모든 Place의 DNA / 이미지 상태 감사."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.models import Place, PlaceImage, PlaceSpaceDNA


def main() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(
                Place.id,
                Place.name,
                func.count(PlaceImage.id).label("img_count"),
                PlaceSpaceDNA.place_id.label("dna_pid"),
            )
            .outerjoin(PlaceImage, PlaceImage.place_id == Place.id)
            .outerjoin(PlaceSpaceDNA, PlaceSpaceDNA.place_id == Place.id)
            .group_by(Place.id, Place.name, PlaceSpaceDNA.place_id)
            .order_by(Place.id.asc())
            .all()
        )

        have_dna = [r for r in rows if r.dna_pid is not None]
        no_dna = [r for r in rows if r.dna_pid is None]

        print(f"=== DNA 있음 ({len(have_dna)}개) ===")
        for r in have_dna:
            try:
                name = r.name
            except Exception:
                name = "?"
            print(f"  id={r.id:>3}  imgs={r.img_count}  name={name}")

        print(f"\n=== DNA 없음 ({len(no_dna)}개) ===")
        for r in no_dna:
            try:
                name = r.name
            except Exception:
                name = "?"
            reason = "이미지 없음" if r.img_count == 0 else "이미지 있는데 분석 실패(URL 만료 의심)"
            print(f"  id={r.id:>3}  imgs={r.img_count}  name={name}  [{reason}]")
    finally:
        db.close()


if __name__ == "__main__":
    main()
