from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Spot, Storage, StorageMember, User
from app.schemas.spot import SpotCreate, SpotResponse, SpotUpdate

router = APIRouter(prefix="/storages/{storage_id}/spots", tags=["spots"])


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


@router.get("", response_model=list[SpotResponse])
def list_spots(
    storage_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_member(storage_id, db, current_user)
    return (
        db.query(Spot)
        .filter(Spot.storage_id == storage_id, Spot.deleted_at.is_(None))
        .all()
    )


@router.post("", response_model=SpotResponse, status_code=status.HTTP_201_CREATED)
def create_spot(
    storage_id: int,
    body: SpotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_member(storage_id, db, current_user, required_roles=("owner", "editor"))

    # 같은 창고에 동일 장소 중복 저장 방지
    existing = db.query(Spot).filter(
        Spot.storage_id == storage_id,
        Spot.place_id == body.place_id,
        Spot.deleted_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="이미 이 창고에 저장된 장소입니다.")

    spot = Spot(
        storage_id=storage_id,
        added_by=current_user.id,
        **body.model_dump(),
    )
    db.add(spot)
    db.commit()
    db.refresh(spot)
    return spot


@router.get("/{spot_id}", response_model=SpotResponse)
def get_spot(
    storage_id: int,
    spot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_member(storage_id, db, current_user)
    spot = db.query(Spot).filter(
        Spot.id == spot_id,
        Spot.storage_id == storage_id,
        Spot.deleted_at.is_(None),
    ).first()
    if not spot:
        raise HTTPException(status_code=404, detail="스팟을 찾을 수 없습니다.")
    return spot


@router.put("/{spot_id}", response_model=SpotResponse)
def update_spot(
    storage_id: int,
    spot_id: int,
    body: SpotUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_member(storage_id, db, current_user, required_roles=("owner", "editor"))
    spot = db.query(Spot).filter(
        Spot.id == spot_id,
        Spot.storage_id == storage_id,
        Spot.deleted_at.is_(None),
    ).first()
    if not spot:
        raise HTTPException(status_code=404, detail="스팟을 찾을 수 없습니다.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(spot, field, value)

    # 방문 처리: is_visited가 True로 변경되면 visited_at 자동 기록
    if body.is_visited is True and not spot.visited_at:
        spot.visited_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(spot)
    return spot


@router.delete("/{spot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_spot(
    storage_id: int,
    spot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_member(storage_id, db, current_user, required_roles=("owner", "editor"))
    spot = db.query(Spot).filter(
        Spot.id == spot_id,
        Spot.storage_id == storage_id,
        Spot.deleted_at.is_(None),
    ).first()
    if not spot:
        raise HTTPException(status_code=404, detail="스팟을 찾을 수 없습니다.")
    spot.deleted_at = datetime.now(timezone.utc)
    db.commit()
