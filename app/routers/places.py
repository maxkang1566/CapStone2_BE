from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Place, User
from app.schemas.place import PlaceResponse

router = APIRouter(prefix="/places", tags=["places"])


@router.get("", response_model=list[PlaceResponse])
def search_places(
    q: str = Query(..., min_length=1, description="장소명 검색어"),
    page: int = Query(1, ge=1, description="페이지 번호"),
    size: int = Query(20, ge=1, le=100, description="페이지당 항목 수"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """장소명으로 장소를 검색합니다. Spot 생성 시 place_id를 얻는 데 사용합니다."""
    return (
        db.query(Place)
        .filter(Place.name.ilike(f"%{q}%"))
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )


@router.get("/{place_id}", response_model=PlaceResponse)
def get_place(
    place_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """특정 장소의 상세 정보를 반환합니다."""
    place = db.query(Place).filter(Place.id == place_id).first()
    if not place:
        raise HTTPException(status_code=404, detail="장소를 찾을 수 없습니다.")
    return place
