"""핀 핸들러 직접 호출 검증.

`list_my_pins`을 SessionLocal + 가짜 Response로 직접 실행해
6 케이스(단일 storage / 다중 / bbox 유무 / visited 유무 / 권한 거부 / truncation 검증)의
응답 형태와 헤더를 출력한다.

사용:
    poetry run python scripts/_oneoff_check_pins.py --user-id 3 --storage-ids 1
    poetry run python scripts/_oneoff_check_pins.py --user-id 3 --storage-ids 1,2
"""

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.models.models import User
from app.routers.users import list_my_pins


class _FakeResponse:
    """FastAPI Response의 headers 인터페이스만 흉내."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def _call(db, user, storage_ids: str, visited=None, bbox=None) -> None:
    label = f"storage_ids={storage_ids!r} visited={visited!r} bbox={bbox!r}"
    print(f"--- {label} ---")
    fake_resp = _FakeResponse()
    try:
        result = list_my_pins(
            response=fake_resp,
            storage_ids=storage_ids,
            visited=visited,
            bbox=bbox,
            db=db,
            current_user=user,
        )
    except HTTPException as e:
        print(f"  HTTP {e.status_code}: {e.detail}")
        print()
        return
    print(f"  반환 핀 수: {len(result)}")
    print(f"  X-Truncated: {fake_resp.headers.get('X-Truncated', '<없음>')}")
    for i, pin in enumerate(result[:3]):
        print(
            f"  [{i}] spot_id={pin.spot_id} storage_id={pin.storage_id} "
            f"place_id={pin.place_id} name={pin.name!r} "
            f"lat={pin.latitude:.4f} lng={pin.longitude:.4f} "
            f"visited={pin.is_visited}"
        )
    if len(result) > 3:
        print(f"  ... 그 외 {len(result) - 3}건")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--storage-ids", type=str, default=None,
                        help="콤마 구분 storage ID. 미지정 시 본인 멤버 storage 중 첫 번째 사용.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user_obj = db.get(User, args.user_id)
        if user_obj is None:
            print(f"user_id={args.user_id} 가 DB에 없음.")
            return
        # 인증 의존성 우회: 핸들러 시그니처가 User 인스턴스만 받으면 충분.
        fake_user = SimpleNamespace(id=user_obj.id, email=user_obj.email)

        target_ids = args.storage_ids
        if target_ids is None:
            from app.models.models import StorageMember
            mine = (
                db.query(StorageMember.storage_id)
                .filter(StorageMember.user_id == args.user_id)
                .limit(1)
                .all()
            )
            if not mine:
                print(f"user_id={args.user_id}는 어떤 창고에도 멤버가 아님.")
                return
            target_ids = str(mine[0][0])
            print(f"[자동 선택된 storage_ids: {target_ids}]\n")

        _call(db, fake_user, target_ids)
        _call(db, fake_user, target_ids, visited=True)
        _call(db, fake_user, target_ids, visited=False)
        _call(db, fake_user, target_ids, bbox="124,33,132,39")
        _call(db, fake_user, target_ids, bbox="0,0,1,1")
        _call(db, fake_user, "99999999")  # 비멤버 storage_id → 404 기대
        _call(db, fake_user, "abc")  # 형식 오류 → 422 기대
        _call(db, fake_user, target_ids, bbox="1,2,3")  # bbox 형식 오류 → 422 기대
    finally:
        db.close()


if __name__ == "__main__":
    main()
