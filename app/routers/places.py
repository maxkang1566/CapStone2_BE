from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from geoalchemy2.elements import WKTElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import (
    Place,
    PlaceImage,
    PlaceRawData,
    PlaceReview,
    PlaceSpaceDNA,
    User,
)
from app.schemas.dna import PlaceSpaceDNAResponse
from app.schemas.place import (
    NaverPlaceUpsertRequest,
    NaverPlaceUpsertResponse,
    PlaceImageResponse,
    PlaceRawDataResponse,
    PlaceResponse,
    PlaceReviewResponse,
)
from app.services.naver_blog import collect_reviews_for_place

router = APIRouter(prefix="/places", tags=["places"])


@router.post("/from-naver", response_model=NaverPlaceUpsertResponse, status_code=200)
def upsert_place_from_naver(
    body: NaverPlaceUpsertRequest,
    background_tasks: BackgroundTasks,
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

        query = f"{body.name} {body.address}".strip() if body.address else body.name
        background_tasks.add_task(
            collect_reviews_for_place,
            place.id,
            query,
            raw_data.id,
        )
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


@router.get("/{place_id}/images", response_model=list[PlaceImageResponse])
def get_place_images(
    place_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """특정 장소에 저장된 이미지 전체를 반환합니다.

    한 장소는 인스타 캐러셀·다중 장소 분류·네이버 직접 저장 등으로 여러 장의
    이미지를 가질 수 있다(`place_images` 다중 행). 기존 `Spot.thumbnail_url`/
    `PinResponse.thumbnail_url`은 대표 1장만 노출하므로, 갤러리 표시는 이 엔드포인트로
    lazy 로딩한다.

    정렬: `is_representative DESC`(대표 먼저), `created_at ASC`(저장된 순) —
    Space DNA 분석기의 `_pick_image_urls`와 동일 정렬이라 항상 대표 이미지가 첫 번째.
    """
    place = db.query(Place).filter(Place.id == place_id).first()
    if not place:
        raise HTTPException(status_code=404, detail="장소를 찾을 수 없습니다.")
    return (
        db.query(PlaceImage)
        .filter(PlaceImage.place_id == place_id)
        .order_by(PlaceImage.is_representative.desc(), PlaceImage.created_at.asc())
        .all()
    )


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


@router.get("/{place_id}/reviews", response_model=list[PlaceReviewResponse])
def get_place_reviews(
    place_id: int,
    page: int = Query(1, ge=1, description="페이지 번호"),
    size: int = Query(20, ge=1, le=100, description="페이지당 항목 수"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """특정 장소에 수집된 리뷰(네이버 블로그 등) 목록을 반환합니다."""
    place = db.query(Place).filter(Place.id == place_id).first()
    if not place:
        raise HTTPException(status_code=404, detail="장소를 찾을 수 없습니다.")
    return (
        db.query(PlaceReview)
        .filter(PlaceReview.place_id == place_id)
        .order_by(PlaceReview.collected_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )


@router.get("/{place_id}/space-dna", response_model=PlaceSpaceDNAResponse)
def get_place_space_dna(
    place_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """장소의 공간 DNA(MBTI 4축 + 신뢰도)를 반환합니다.

    AI팀이 아직 분석하지 않은 장소는 `has_data=False`로 응답합니다.
    """
    place = db.query(Place).filter(Place.id == place_id).first()
    if not place:
        raise HTTPException(status_code=404, detail="장소를 찾을 수 없습니다.")
    dna = (
        db.query(PlaceSpaceDNA)
        .filter(PlaceSpaceDNA.place_id == place_id)
        .first()
    )
    # 행이 없거나 AI팀이 행만 만들고 mbti_axes가 비어 있으면 동일하게 has_data=False.
    if not dna or not dna.mbti_axes:
        return PlaceSpaceDNAResponse(
            has_data=False,
            ai_summary=dna.ai_summary if dna else None,
            updated_at=dna.updated_at if dna else None,
        )
    return PlaceSpaceDNAResponse(
        has_data=True,
        mbti_axes=dna.mbti_axes,
        ai_summary=dna.ai_summary,
        updated_at=dna.updated_at,
    )
