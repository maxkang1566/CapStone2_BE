import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import asc, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Place, Spot, Storage, StorageMember, User, UserSpaceDNA
from app.schemas.dna import UserSpaceDNAResponse
from app.schemas.pin import PinResponse
from app.schemas.user import UserResponse, UserSearchResponse, UserUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

PIN_RESPONSE_CAP = 1000


def _parse_csv_ints(raw: str, field: str) -> list[int]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise HTTPException(status_code=422, detail=f"{field}는 비어 있을 수 없습니다.")
    try:
        return [int(p) for p in parts]
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"{field}는 콤마로 구분된 정수여야 합니다(예: 1,3,5).",
        )


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise HTTPException(
            status_code=422,
            detail="bbox는 swLng,swLat,neLng,neLat 4개 float이어야 합니다.",
        )
    try:
        sw_lng, sw_lat, ne_lng, ne_lat = (float(p) for p in parts)
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox 값은 모두 float이어야 합니다.")
    if not (sw_lng < ne_lng and sw_lat < ne_lat):
        raise HTTPException(
            status_code=422,
            detail="bbox는 sw 좌표가 ne 좌표보다 작아야 합니다(swLng<neLng, swLat<neLat).",
        )
    return sw_lng, sw_lat, ne_lng, ne_lat


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


@router.get("/me/space-dna", response_model=UserSpaceDNAResponse)
def get_my_space_dna(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """내 공간 DNA(MBTI 4축 + 누적 방문 수)를 반환합니다.

    아직 방문 체크인이 없거나 분석되지 않은 사용자는 `has_data=False`로 응답합니다.
    """
    dna = (
        db.query(UserSpaceDNA)
        .filter(UserSpaceDNA.user_id == current_user.id)
        .first()
    )
    # 행이 없거나(아직 한 번도 트리거 안 됨) mbti_axes가 빈 dict(visited 0건 또는
    # 합산 가능한 PlaceSpaceDNA가 0건)면 동일하게 has_data=False로 정규화.
    if not dna or not dna.mbti_axes:
        return UserSpaceDNAResponse(
            has_data=False,
            total_visits=dna.total_visits if dna else 0,
            last_analyzed=dna.last_analyzed if dna else None,
        )
    return UserSpaceDNAResponse(
        has_data=True,
        mbti_axes=dna.mbti_axes,
        preferred_vibe_tags=dna.preferred_vibe_tags,
        total_visits=dna.total_visits,
        last_analyzed=dna.last_analyzed,
    )


@router.get("/me/pins", response_model=list[PinResponse])
def list_my_pins(
    response: Response,
    storage_ids: str = Query(
        ...,
        description="조회할 창고 ID CSV(예: 1,3,5). 본인이 멤버인 창고만 허용.",
    ),
    visited: bool | None = Query(
        None,
        description="True=방문 체크인된 spot만 / False=미방문만 / 미지정=전체",
    ),
    bbox: str | None = Query(
        None,
        description="지도 viewport 좌표 swLng,swLat,neLng,neLat. 미지정 시 전체.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """선택한 창고들에 저장된 spot을 지도 마커용 슬림 형식으로 반환합니다.

    - 좌표가 없는 장소는 응답에서 제외(지도에 핀을 못 박으므로).
    - storage_ids에 본인이 멤버가 아닌 ID가 1개라도 있으면 404 + inaccessible_ids.
    - 결과가 1000건을 초과하면 1000건으로 자르고 `X-Truncated: true` 헤더를 추가.
    """
    storage_id_list = _parse_csv_ints(storage_ids, "storage_ids")
    bbox_tuple = _parse_bbox(bbox) if bbox is not None else None

    member_ids = {
        sid
        for (sid,) in db.query(StorageMember.storage_id)
        .filter(
            StorageMember.user_id == current_user.id,
            StorageMember.storage_id.in_(storage_id_list),
        )
        .all()
    }
    inaccessible = [sid for sid in storage_id_list if sid not in member_ids]
    if inaccessible:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "접근 권한이 없는 창고가 포함됨",
                "inaccessible_ids": inaccessible,
            },
        )

    query = (
        db.query(
            Spot.id.label("spot_id"),
            Spot.storage_id,
            Spot.place_id,
            Place.name,
            func.ST_Y(Place.coordinate).label("latitude"),
            func.ST_X(Place.coordinate).label("longitude"),
            Spot.is_visited,
            Spot.thumbnail_url,
        )
        .join(Place, Place.id == Spot.place_id)
        .join(Storage, Storage.id == Spot.storage_id)
        .filter(
            Spot.storage_id.in_(storage_id_list),
            Spot.deleted_at.is_(None),
            Storage.deleted_at.is_(None),
            Place.coordinate.is_not(None),
        )
    )
    if visited is not None:
        query = query.filter(Spot.is_visited == visited)
    if bbox_tuple is not None:
        sw_lng, sw_lat, ne_lng, ne_lat = bbox_tuple
        envelope = func.ST_MakeEnvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
        # `&&` 연산자가 GIST 인덱스(idx_places_coordinate)를 사용한다.
        query = query.filter(envelope.op("&&")(Place.coordinate))

    # 1001건까지 받아서 truncation 발생 여부를 감지한다.
    rows = query.limit(PIN_RESPONSE_CAP + 1).all()
    truncated = len(rows) > PIN_RESPONSE_CAP
    if truncated:
        response.headers["X-Truncated"] = "true"
        logger.info(
            "/users/me/pins truncated user_id=%d storage_ids=%s",
            current_user.id,
            storage_id_list,
        )
        rows = rows[:PIN_RESPONSE_CAP]

    return [
        PinResponse(
            spot_id=r.spot_id,
            storage_id=r.storage_id,
            place_id=r.place_id,
            name=r.name,
            latitude=r.latitude,
            longitude=r.longitude,
            is_visited=r.is_visited,
            thumbnail_url=r.thumbnail_url,
        )
        for r in rows
    ]


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
