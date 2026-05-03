from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.elements import WKTElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Place, PlaceRawData, User
from app.schemas.place import (
    NaverPlaceUpsertRequest,
    NaverPlaceUpsertResponse,
    PlaceRawDataResponse,
    PlaceResponse,
)

router = APIRouter(prefix="/places", tags=["places"])


@router.post("/from-naver", response_model=NaverPlaceUpsertResponse, status_code=200)
def upsert_place_from_naver(
    body: NaverPlaceUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """네이버 지도 장소 ID 기준으로 Place를 찾거나 생성합니다."""
    existing_raw = (
        db.query(PlaceRawData)
        .filter(
            PlaceRawData.provider == "naver",
            PlaceRawData.provider_place_id == body.naver_place_id,
        )
        .first()
    )
    if existing_raw:
        return NaverPlaceUpsertResponse(
            place_id=existing_raw.place_id,
            created=False,
            place=existing_raw.place,
        )

    coordinate = None
    if body.latitude is not None and body.longitude is not None:
        coordinate = WKTElement(f"POINT({body.longitude} {body.latitude})", srid=4326)

    try:
        place = Place(
            name=body.name,
            address=body.address,
            coordinate=coordinate,
            category_group=body.category_group,
            phone=body.phone,
            homepage_url=body.homepage_url,
        )
        db.add(place)
        db.flush()

        raw_data = PlaceRawData(
            place_id=place.id,
            provider="naver",
            provider_place_id=body.naver_place_id,
            raw_payload=body.raw_payload,
        )
        db.add(raw_data)
        db.commit()
        db.refresh(place)
        return NaverPlaceUpsertResponse(place_id=place.id, created=True, place=place)

    except IntegrityError:
        db.rollback()
        raw_data = (
            db.query(PlaceRawData)
            .filter(
                PlaceRawData.provider == "naver",
                PlaceRawData.provider_place_id == body.naver_place_id,
            )
            .first()
        )
        return NaverPlaceUpsertResponse(
            place_id=raw_data.place_id,
            created=False,
            place=raw_data.place,
        )


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


@router.get("/{place_id}/raw-data", response_model=list[PlaceRawDataResponse])
def get_place_raw_data(
    place_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """특정 장소에 연결된 원천 데이터(인스타그램 등) 목록을 반환합니다."""
    place = db.query(Place).filter(Place.id == place_id).first()
    if not place:
        raise HTTPException(status_code=404, detail="장소를 찾을 수 없습니다.")
    return (
        db.query(PlaceRawData)
        .filter(PlaceRawData.place_id == place_id)
        .order_by(PlaceRawData.collected_at.desc())
        .all()
    )
