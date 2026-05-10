from fastapi import APIRouter, Depends, Query
from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import User
from app.schemas.user import UserResponse, UserSearchResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserResponse)
def update_me(
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.nickname is not None:
        current_user.nickname = body.nickname
    if body.profile_image is not None:
        current_user.profile_image = body.profile_image
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/search", response_model=list[UserSearchResponse])
def search_users(
    q: str = Query(..., min_length=1, max_length=50, description="닉네임 prefix"),
    size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """창고 멤버 초대용 닉네임 prefix 검색. 본인 제외, 닉네임 보유 유저만.

    카카오 미동의 사용자는 임시 이메일(`kakao_{id}@picklog.local`)이라
    이메일 검색이 불가능 — 닉네임 기반 친구 찾기가 사실상 유일한 방법.
    """
    return (
        db.query(User)
        .filter(
            User.id != current_user.id,
            User.nickname.isnot(None),
            User.nickname.ilike(f"{q}%"),
        )
        .order_by(asc(User.nickname))
        .limit(size)
        .all()
    )
