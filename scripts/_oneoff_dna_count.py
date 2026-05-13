"""백필 결과 확인 — place_space_dna 행 수, mbti_axes 키 분포, place_tags 분포."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.models import Place, PlaceImage, PlaceSpaceDNA, PlaceTag, Tag


def main() -> None:
    db = SessionLocal()
    try:
        total_places = db.query(Place).count()
        total_dna = db.query(PlaceSpaceDNA).count()
        total_with_image = (
            db.query(Place.id).join(PlaceImage, PlaceImage.place_id == Place.id)
            .distinct().count()
        )
        total_no_dna = (
            db.query(Place.id).outerjoin(
                PlaceSpaceDNA, PlaceSpaceDNA.place_id == Place.id
            ).filter(PlaceSpaceDNA.place_id.is_(None)).count()
        )
        print(f"places total           = {total_places}")
        print(f"  with PlaceImage      = {total_with_image}")
        print(f"  with place_space_dna = {total_dna}")
        print(f"  missing DNA          = {total_no_dna}")

        sample = db.query(PlaceSpaceDNA.place_id, PlaceSpaceDNA.mbti_axes).limit(5).all()
        print("\nsamples (first 5):")
        for pid, axes in sample:
            print(f"  place_id={pid} axes={json.dumps(axes, ensure_ascii=False)}")

        key_counter: Counter[str] = Counter()
        for (axes,) in db.query(PlaceSpaceDNA.mbti_axes).all():
            if isinstance(axes, dict):
                key_counter.update(axes.keys())
        print(f"\nmbti_axes keys observed: {dict(key_counter)}")

        tag_count = db.query(Tag).count()
        place_tag_count = db.query(PlaceTag).count()
        print(f"\ntags master = {tag_count}, place_tags rows = {place_tag_count}")
        top_names = (
            db.query(Tag.name, func.count(PlaceTag.tag_id).label("n"))
            .join(PlaceTag, PlaceTag.tag_id == Tag.id)
            .group_by(Tag.name)
            .order_by(func.count(PlaceTag.tag_id).desc())
            .limit(10).all()
        )
        print("top tags:")
        for name, n in top_names:
            print(f"  {n:>3}x {name}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
