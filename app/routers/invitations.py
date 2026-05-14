"""창고 멤버 토큰 초대 링크.

엔드포인트:
  owner 측 (/storages/{storage_id}/invitations 트리)
    - POST   생성 (토큰 발급)
    - GET    활성 초대 목록
    - DELETE 취소(revoke)

  수신자 측 (/invitations/{token} 트리)
    - GET    /invitations/{token}         preview
    - POST   /invitations/{token}/accept  멤버 가입
    - POST   /invitations/{token}/decline 거절 (204 no-op)

설계 결정은 plan 파일 + notes/2026-05-11-storage-invitations.md 참조.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Storage, StorageInvitation, StorageMember, User
from app.routers.storages import _get_member, _to_member_detail
from app.schemas.invitation import (
    InvitationCreateRequest,
    InvitationPreviewResponse,
    InvitationResponse,
)
from app.schemas.storage import StorageMemberDetailResponse

router = APIRouter(tags=["invitations"])


# ---------- 헬퍼 ----------


def _generate_token() -> str:
    """256-bit entropy URL-safe 토큰. 충돌 확률 무시 가능 + DB unique 제약 안전망."""
    return secrets.token_urlsafe(32)


def _get_invitation_by_token(token: str, db: Session) -> StorageInvitation:
    inv = (
        db.query(StorageInvitation)
        .filter(StorageInvitation.token == token)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="초대를 찾을 수 없습니다.")
    return inv


def _check_invitation_active(inv: StorageInvitation) -> None:
    """revoked_at IS NULL AND expires_at > NOW() 검증. 실패 시 410."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # expires_at은 마이그레이션이 TIMESTAMP WITHOUT TIME ZONE으로 저장하므로
    # naive datetime으로 비교 (DB 컬럼 정의와 일치).
    if inv.revoked_at is not None or inv.expires_at <= now:
        raise HTTPException(
            status_code=410, detail="초대가 만료되었거나 취소되었습니다."
        )


def _check_storage_alive(storage: Storage) -> None:
    if storage is None or storage.deleted_at is not None:
        raise HTTPException(status_code=404, detail="저장소를 찾을 수 없습니다.")


def _to_invitation_response(inv: StorageInvitation) -> InvitationResponse:
    return InvitationResponse(
        id=inv.id,
        token=inv.token,
        storage_id=inv.storage_id,
        role=inv.role,
        expires_at=inv.expires_at,
        revoked_at=inv.revoked_at,
        created_at=inv.created_at,
        invited_by_nickname=inv.inviter.nickname if inv.inviter else None,
    )


# ---------- owner 측 ----------


@router.post(
    "/storages/{storage_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    storage_id: int,
    body: InvitationCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """초대 토큰 생성 (owner 전용). 활성 토큰 중복 정책: 매번 새로 발급."""
    member = _get_member(storage_id, db, current_user, required_roles=("owner",))
    _check_storage_alive(member.storage)

    # naive UTC — 컬럼이 TIMESTAMP WITHOUT TIME ZONE이라 일관성 유지
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        days=body.expires_in_days
    )

    inv = StorageInvitation(
        token=_generate_token(),
        storage_id=storage_id,
        invited_by=current_user.id,
        role=body.role,
        expires_at=expires_at,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    inv.inviter = current_user
    return _to_invitation_response(inv)
    return _to_invitation_response(inv)


@router.get(
    "/storages/{storage_id}/invitations",
    response_model=list[InvitationResponse],
)
def list_invitations(
    storage_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """활성 초대 목록 (revoked/만료 제외, owner 전용). history는 후속 작업."""
    member = _get_member(storage_id, db, current_user, required_roles=("owner",))
    _check_storage_alive(member.storage)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (
        db.query(StorageInvitation)
        .options(joinedload(StorageInvitation.inviter))
        .filter(
            StorageInvitation.storage_id == storage_id,
            StorageInvitation.revoked_at.is_(None),
            StorageInvitation.expires_at > now,
        )
        .order_by(StorageInvitation.created_at.desc())
        .all()
    )
    return [_to_invitation_response(r) for r in rows]


@router.delete(
    "/storages/{storage_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_invitation(
    storage_id: int,
    invitation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """초대 취소 (owner 전용). 이미 취소/만료여도 멱등적으로 204."""
    member = _get_member(storage_id, db, current_user, required_roles=("owner",))
    _check_storage_alive(member.storage)

    inv = (
        db.query(StorageInvitation)
        .filter(
            StorageInvitation.id == invitation_id,
            StorageInvitation.storage_id == storage_id,
        )
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="초대를 찾을 수 없습니다.")

    if inv.revoked_at is None:
        inv.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()


# ---------- 수신자 측 ----------


@router.get(
    "/invitations/{token}",
    response_model=InvitationPreviewResponse,
)
def preview_invitation(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """초대 미리보기. 인증 필수 — 토큰만 가지고 storage 메타 노출 방지."""
    inv = (
        db.query(StorageInvitation)
        .options(
            joinedload(StorageInvitation.inviter),
            joinedload(StorageInvitation.storage),
        )
        .filter(StorageInvitation.token == token)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="초대를 찾을 수 없습니다.")
    _check_invitation_active(inv)
    _check_storage_alive(inv.storage)

    return InvitationPreviewResponse(
        storage_id=inv.storage_id,
        storage_title=inv.storage.title,
        role=inv.role,
        inviter_nickname=inv.inviter.nickname if inv.inviter else None,
        expires_at=inv.expires_at,
    )


@router.post(
    "/invitations/{token}/accept",
    response_model=StorageMemberDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def accept_invitation(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """토큰 수락 → 멤버 가입. 이미 멤버면 409."""
    inv = _get_invitation_by_token(token, db)
    _check_invitation_active(inv)
    storage = db.query(Storage).filter(Storage.id == inv.storage_id).first()
    _check_storage_alive(storage)

    existing = (
        db.query(StorageMember)
        .filter(
            StorageMember.storage_id == inv.storage_id,
            StorageMember.user_id == current_user.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="이미 저장소 멤버입니다.")

    new_member = StorageMember(
        storage_id=inv.storage_id,
        user_id=current_user.id,
        role=inv.role,
    )
    db.add(new_member)
    try:
        db.commit()
    except IntegrityError:
        # 동시 클릭 레이스: 같은 user가 두 번 accept를 거의 동시에 호출한 경우.
        # uq_storage_members_storage_user가 두 번째 INSERT를 차단한다.
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 저장소 멤버입니다.")

    # _to_member_detail은 member.user 관계가 필요하므로 refresh 후 명시 로드
    db.refresh(new_member)
    new_member.user = current_user  # 이미 메모리에 있는 인증 사용자 재사용
    return _to_member_detail(new_member)


@router.post(
    "/invitations/{token}/decline",
    status_code=status.HTTP_204_NO_CONTENT,
)
def decline_invitation(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """거절. 멀티유저 링크 모델이라 per-user decline 기록은 의미 모호 — 204 no-op.

    엔드포인트는 클라이언트 시맨틱(수락/거절 짝맞춤) 유지를 위해 노출.
    토큰 자체는 무효 토큰(404)·만료/취소(410)일 때 명확히 알려서 클라이언트가 안내 메시지를 띄울 수 있게 한다.
    """
    inv = _get_invitation_by_token(token, db)
    _check_invitation_active(inv)
    # no-op
