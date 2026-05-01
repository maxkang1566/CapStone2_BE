from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Storage, StorageMember, User
from app.schemas.storage import StorageCreate, StorageResponse, StorageUpdate

router = APIRouter(prefix="/storages", tags=["storages"])


def _get_member(
    storage_id: int,
    db: Session,
    current_user: User,
    required_roles: tuple = ("owner", "editor", "viewer"),
) -> StorageMember:
    """현재 유저가 해당 창고의 멤버인지 확인하고 멤버 객체를 반환합니다."""
    member = db.query(StorageMember).filter(
        StorageMember.storage_id == storage_id,
        StorageMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="저장소를 찾을 수 없습니다.")
    if member.role not in required_roles:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    return member


@router.get("", response_model=list[StorageResponse])
def list_storages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """현재 유저가 멤버로 속한 모든 창고를 반환합니다 (소프트 삭제된 창고 제외)."""
    return (
        db.query(Storage)
        .join(StorageMember, Storage.id == StorageMember.storage_id)
        .filter(
            StorageMember.user_id == current_user.id,
            Storage.deleted_at.is_(None),
        )
        .all()
    )


@router.post("", response_model=StorageResponse, status_code=status.HTTP_201_CREATED)
def create_storage(
    body: StorageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    storage = Storage(**body.model_dump())
    db.add(storage)
    db.flush()  # storage.id 확보

    member = StorageMember(storage_id=storage.id, user_id=current_user.id, role="owner")
    db.add(member)

    db.commit()
    db.refresh(storage)
    return storage


@router.get("/{storage_id}", response_model=StorageResponse)
def get_storage(
    storage_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = _get_member(storage_id, db, current_user)
    return member.storage


@router.put("/{storage_id}", response_model=StorageResponse)
def update_storage(
    storage_id: int,
    body: StorageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = _get_member(storage_id, db, current_user, required_roles=("owner", "editor"))
    storage = member.storage
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(storage, field, value)
    db.commit()
    db.refresh(storage)
    return storage


@router.delete("/{storage_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_storage(
    storage_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = _get_member(storage_id, db, current_user, required_roles=("owner",))
    member.storage.deleted_at = datetime.now(timezone.utc)
    db.commit()
