"""storage_invitations 6개 엔드포인트 end-to-end 검증.

TestClient + get_current_user dependency override로 인증 우회.
임시 user 3명 + storage 1개 생성해 흐름 검증 후 모두 정리.

실행:
    poetry run python scripts/_oneoff_check_invitations.py

성공 시 모든 케이스에 [OK] 표시. 실패 케이스가 1건이라도 있으면 비-0 종료 코드.
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.dependencies.auth import get_current_user
from app.main import app
from app.models.models import Storage, StorageInvitation, StorageMember, User

PREFIX = "__inv_test__"


_failures: list[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    mark = "OK   " if cond else "FAIL "
    extra = f" — {detail}" if detail else ""
    print(f"  [{mark}] {label}{extra}")
    if not cond:
        _failures.append(label)


def _setup(db) -> tuple[User, User, User, Storage]:
    """오너 + invitee 2명 + 빈 storage. owner는 storage_members에 추가."""
    owner = User(email=f"{PREFIX}owner@example.com", nickname=f"{PREFIX}owner")
    inv1 = User(email=f"{PREFIX}inv1@example.com", nickname=f"{PREFIX}inv1")
    inv2 = User(email=f"{PREFIX}inv2@example.com", nickname=f"{PREFIX}inv2")
    db.add_all([owner, inv1, inv2])
    db.flush()
    storage = Storage(title=f"{PREFIX}box", description="test")
    db.add(storage)
    db.flush()
    db.add(StorageMember(storage_id=storage.id, user_id=owner.id, role="owner"))
    db.commit()
    return owner, inv1, inv2, storage


def _cleanup(db) -> None:
    """PREFIX로 만든 모든 row 삭제. storage 삭제 → CASCADE로 invitations·members 정리."""
    db.query(Storage).filter(Storage.title.like(f"{PREFIX}%")).delete(synchronize_session=False)
    db.query(User).filter(User.email.like(f"{PREFIX}%")).delete(synchronize_session=False)
    db.commit()


def main() -> int:
    print("[storage_invitations 검증 시작]\n")

    # 사전 정리 (이전 실행이 비정상 종료해 남긴 row 제거)
    pre_db = SessionLocal()
    try:
        _cleanup(pre_db)
    finally:
        pre_db.close()

    db = SessionLocal()
    try:
        owner, inv1, inv2, storage = _setup(db)
        print(f"setup: owner={owner.id} inv1={inv1.id} inv2={inv2.id} storage={storage.id}\n")

        impersonate = {"id": owner.id}

        # FastAPI의 의존성 캐싱으로 라우터의 db와 같은 세션을 받게 Depends(get_db)를 명시.
        # 라우터에서 attach 충돌(다른 세션의 user를 멤버에 연결)을 피하려면 같은 세션의 user여야 한다.
        def _override_current_user(req_db: Session = Depends(get_db)) -> User:
            return req_db.query(User).filter(User.id == impersonate["id"]).first()

        app.dependency_overrides[get_current_user] = _override_current_user
        client = TestClient(app)

        try:
            # === 1. owner 토큰 발급 ===
            print("[1] owner: POST /storages/{id}/invitations")
            impersonate["id"] = owner.id
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "editor", "expires_in_days": 7},
            )
            _check("201 + 토큰·만료 반환", r.status_code == 201, f"status={r.status_code}")
            body = r.json() if r.status_code == 201 else {}
            token = body.get("token")
            _check("token 길이 > 30", bool(token) and len(token) > 30)
            _check("role=editor 반영", body.get("role") == "editor")
            _check("invited_by_nickname=owner", body.get("invited_by_nickname") == f"{PREFIX}owner")

            # === 2. owner 활성 목록 ===
            print("\n[2] owner: GET /storages/{id}/invitations")
            r = client.get(f"/storages/{storage.id}/invitations")
            _check("200 + 1건", r.status_code == 200 and len(r.json()) == 1)

            # === 3. invitee1 preview ===
            print("\n[3] invitee1: GET /invitations/{token}")
            impersonate["id"] = inv1.id
            r = client.get(f"/invitations/{token}")
            _check("200", r.status_code == 200)
            preview = r.json() if r.status_code == 200 else {}
            _check("storage_title 반영", preview.get("storage_title") == f"{PREFIX}box")
            _check("inviter_nickname=owner", preview.get("inviter_nickname") == f"{PREFIX}owner")

            # === 4. invitee1 accept ===
            print("\n[4] invitee1: POST /invitations/{token}/accept")
            r = client.post(f"/invitations/{token}/accept")
            _check("201", r.status_code == 201, f"status={r.status_code} body={r.text[:200]}")
            mem = (
                db.query(StorageMember)
                .filter(StorageMember.storage_id == storage.id, StorageMember.user_id == inv1.id)
                .first()
            )
            _check("storage_members에 editor로 추가", mem is not None and mem.role == "editor")

            # === 5. invitee1 중복 accept ===
            print("\n[5] invitee1: POST accept 다시 (이미 멤버)")
            r = client.post(f"/invitations/{token}/accept")
            _check("409", r.status_code == 409)

            # === 6. invitee2 accept (멀티유저 동일 토큰) ===
            print("\n[6] invitee2: 같은 토큰으로 accept")
            impersonate["id"] = inv2.id
            r = client.post(f"/invitations/{token}/accept")
            _check("201 (멀티유저 토큰)", r.status_code == 201, f"status={r.status_code}")

            # === 7. 잘못된 토큰들 ===
            print("\n[7] invitee1: 잘못된 토큰 흐름")
            impersonate["id"] = inv1.id
            r = client.get("/invitations/nope-bad-token")
            _check("preview 404", r.status_code == 404)
            r = client.post("/invitations/nope-bad-token/accept")
            _check("accept 404", r.status_code == 404)
            r = client.post("/invitations/nope-bad-token/decline")
            _check("decline 404", r.status_code == 404)

            # === 8. decline 204 ===
            print("\n[8] invitee2: POST /invitations/{token}/decline")
            impersonate["id"] = inv2.id
            r = client.post(f"/invitations/{token}/decline")
            _check("204", r.status_code == 204)

            # === 9. 비-owner가 토큰 생성 시도 ===
            print("\n[9] invitee1: POST /storages/{id}/invitations (editor 권한)")
            impersonate["id"] = inv1.id
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "viewer", "expires_in_days": 7},
            )
            _check("403", r.status_code == 403, f"status={r.status_code}")

            # === 10. validation ===
            print("\n[10] body validation")
            impersonate["id"] = owner.id
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "editor", "expires_in_days": 0},
            )
            _check("expires_in_days=0 → 422", r.status_code == 422)
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "editor", "expires_in_days": 31},
            )
            _check("expires_in_days=31 → 422", r.status_code == 422)
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "owner", "expires_in_days": 7},
            )
            _check("role=owner → 422", r.status_code == 422)

            # === 11. revoke ===
            print("\n[11] owner: DELETE /storages/{id}/invitations/{invitation_id}")
            inv_id = body.get("id") if body else None
            # body는 [10]에서 마지막 422 응답에 덮였을 수 있음 — 재조회
            inv_row = (
                db.query(StorageInvitation)
                .filter(StorageInvitation.token == token)
                .first()
            )
            inv_id = inv_row.id
            r = client.delete(f"/storages/{storage.id}/invitations/{inv_id}")
            _check("204", r.status_code == 204)
            db.refresh(inv_row)
            _check("revoked_at 기록됨", inv_row.revoked_at is not None)

            # === 12. revoke 멱등 ===
            print("\n[12] owner: 같은 invitation 두 번째 DELETE")
            r = client.delete(f"/storages/{storage.id}/invitations/{inv_id}")
            _check("204 (멱등)", r.status_code == 204)

            # === 13. revoke 후 410 ===
            print("\n[13] 취소된 토큰 흐름")
            impersonate["id"] = inv1.id
            r = client.get(f"/invitations/{token}")
            _check("preview 410", r.status_code == 410)
            r = client.post(f"/invitations/{token}/accept")
            _check("accept 410", r.status_code == 410)
            r = client.post(f"/invitations/{token}/decline")
            _check("decline 410", r.status_code == 410)

            # === 14. 만료 토큰 (별도 토큰 발급 후 expires_at 과거로 ===
            print("\n[14] 만료 토큰 흐름")
            impersonate["id"] = owner.id
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "viewer", "expires_in_days": 1},
            )
            assert r.status_code == 201
            expired_token = r.json()["token"]
            expired_id = r.json()["id"]
            past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
            db.query(StorageInvitation).filter(StorageInvitation.id == expired_id).update(
                {"expires_at": past}, synchronize_session=False
            )
            db.commit()
            impersonate["id"] = inv1.id
            r = client.get(f"/invitations/{expired_token}")
            _check("preview 410", r.status_code == 410)
            r = client.post(f"/invitations/{expired_token}/accept")
            _check("accept 410", r.status_code == 410)

            # === 15. 소프트삭제된 storage ===
            print("\n[15] storage 소프트삭제 후 흐름")
            impersonate["id"] = owner.id
            r = client.post(
                f"/storages/{storage.id}/invitations",
                json={"role": "viewer", "expires_in_days": 1},
            )
            assert r.status_code == 201
            soft_token = r.json()["token"]
            db.query(Storage).filter(Storage.id == storage.id).update(
                {"deleted_at": datetime.now(timezone.utc).replace(tzinfo=None)},
                synchronize_session=False,
            )
            db.commit()
            impersonate["id"] = inv1.id
            r = client.get(f"/invitations/{soft_token}")
            _check("소프트삭제 storage preview 404", r.status_code == 404)
            r = client.post(f"/invitations/{soft_token}/accept")
            _check("소프트삭제 storage accept 404", r.status_code == 404)
            # 소프트삭제 복구 (cleanup 단계에서 hard delete 해야 하므로 굳이 복구 안 해도 됨)

        finally:
            app.dependency_overrides.pop(get_current_user, None)

    finally:
        try:
            _cleanup(db)
        finally:
            db.close()

    print("\n[결과]")
    if _failures:
        print(f"  실패 {len(_failures)}건: {_failures}")
        return 1
    print("  ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
