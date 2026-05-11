"""DB 데이터 진단 — pin 검증할 user/storage 조합을 찾는다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.models import Place, Spot, Storage, StorageMember, User


def main() -> None:
    db = SessionLocal()
    try:
        print("전체 카운트:")
        print(f"  users={db.query(User).count()}")
        print(
            f"  storages(active)={db.query(Storage).filter(Storage.deleted_at.is_(None)).count()}"
        )
        print(f"  storage_members={db.query(StorageMember).count()}")
        print(
            f"  spots(non-deleted)={db.query(Spot).filter(Spot.deleted_at.is_(None)).count()}"
        )
        print(
            f"  places(with coord)={db.query(Place).filter(Place.coordinate.is_not(None)).count()}"
        )
        print(
            f"  spots joining place w/ coord="
            f"{db.query(Spot).join(Place, Place.id == Spot.place_id).filter(Spot.deleted_at.is_(None), Place.coordinate.is_not(None)).count()}"
        )
        print()
        print("(user, storage) 조합 별 좌표 보유 spot 수 (top 10):")
        rows = (
            db.query(
                StorageMember.user_id,
                Spot.storage_id,
                func.count(Spot.id).label("n"),
            )
            .join(Spot, Spot.storage_id == StorageMember.storage_id)
            .join(Place, Place.id == Spot.place_id)
            .filter(Spot.deleted_at.is_(None), Place.coordinate.is_not(None))
            .group_by(StorageMember.user_id, Spot.storage_id)
            .order_by(func.count(Spot.id).desc())
            .limit(10)
            .all()
        )
        if not rows:
            print("  (없음)")
        for u, s, n in rows:
            print(f"  user_id={u} storage_id={s} spots_with_coord={n}")
        print()
        print("storage_members 샘플:")
        for sm in db.query(StorageMember).limit(10).all():
            print(f"  user_id={sm.user_id} storage_id={sm.storage_id} role={sm.role}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
