"""DNA 있는 24건의 다른 부속 데이터 완전성 점검.

체크 항목 (핸드오프 doc §3 핵심 테이블 기준):
- Place 기본: address, coordinate, category_group
- PlaceRawData: naver / instagram / naver_blog provider 행 존재 여부
- PlaceImage: 행 수
- PlaceReview: 행 수
- PlaceTag: 행 수
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.models import (
    Place,
    PlaceImage,
    PlaceRawData,
    PlaceReview,
    PlaceSpaceDNA,
    PlaceTag,
)


def main() -> None:
    db = SessionLocal()
    try:
        place_ids = [pid for (pid,) in db.query(PlaceSpaceDNA.place_id).order_by(PlaceSpaceDNA.place_id).all()]

        # 한 번에 다 끌어오기 (n=24 라 안전)
        places = {p.id: p for p in db.query(Place).filter(Place.id.in_(place_ids)).all()}
        img_counts = dict(
            db.query(PlaceImage.place_id, func.count(PlaceImage.id))
            .filter(PlaceImage.place_id.in_(place_ids))
            .group_by(PlaceImage.place_id).all()
        )
        review_counts = dict(
            db.query(PlaceReview.place_id, func.count(PlaceReview.id))
            .filter(PlaceReview.place_id.in_(place_ids))
            .group_by(PlaceReview.place_id).all()
        )
        tag_counts = dict(
            db.query(PlaceTag.place_id, func.count(PlaceTag.tag_id))
            .filter(PlaceTag.place_id.in_(place_ids))
            .group_by(PlaceTag.place_id).all()
        )
        # provider별 raw
        raw_by_pid: dict[int, set[str]] = {pid: set() for pid in place_ids}
        for pid, provider in db.query(PlaceRawData.place_id, PlaceRawData.provider).filter(
            PlaceRawData.place_id.in_(place_ids)
        ).all():
            raw_by_pid[pid].add(provider)

        header = f"{'id':>3}  {'addr':>4} {'coord':>5} {'cat':>3}  {'naver':>5} {'insta':>5} {'blog':>4}  {'imgs':>4} {'revs':>4} {'tags':>4}  status"
        print(header)
        print("-" * len(header))

        all_ok = True
        for pid in place_ids:
            p = places.get(pid)
            has_addr = bool(p and p.address)
            has_coord = bool(p and p.coordinate is not None)
            has_cat = bool(p and p.category_group)
            providers = raw_by_pid.get(pid, set())
            has_naver = "naver" in providers
            has_insta = "instagram" in providers
            has_blog = "naver_blog" in providers
            imgs = img_counts.get(pid, 0)
            revs = review_counts.get(pid, 0)
            tags = tag_counts.get(pid, 0)

            # 정상 기준: addr+coord+cat + naver/insta raw + img>=1 + review>=1 + tag>=1
            ok = (has_addr and has_coord and has_cat
                  and has_naver and has_insta
                  and imgs >= 1 and revs >= 1 and tags >= 1)
            if not ok:
                all_ok = False

            def y(v: bool) -> str:
                return "O" if v else "x"

            print(
                f"{pid:>3}  "
                f"{y(has_addr):>4} {y(has_coord):>5} {y(has_cat):>3}  "
                f"{y(has_naver):>5} {y(has_insta):>5} {y(has_blog):>4}  "
                f"{imgs:>4} {revs:>4} {tags:>4}  "
                f"{'OK' if ok else 'GAP'}"
            )

        print()
        if all_ok:
            print("=> 24건 모두 정상 (주소/좌표/카테고리 + naver·insta raw + 이미지·리뷰·태그·DNA 보유)")
        else:
            print("=> 일부 GAP 발견 — 위 표의 'x' 컬럼 확인")
    finally:
        db.close()


if __name__ == "__main__":
    main()
