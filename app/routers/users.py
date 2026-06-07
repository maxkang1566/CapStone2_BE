import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import asc, cast, func
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Place, Spot, Storage, StorageMember, User, UserSpaceDNA
from app.schemas.dna import (
    AXIS_TYPES,
    UserSpaceDNAOnboardingRequest,
    UserSpaceDNAResponse,
)
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


def _normalize_axes_to_pairs(raw: dict | None) -> dict | None:
    """저장 형태(단일 값 또는 중첩 dict)를 응답용 중첩 dict로 통일한다.

    - 온보딩 POST 결과는 이미 `{axis: {type_a: x, type_b: 100-x}}` 형태.
    - AI 자동 트리거(`rebuild_user_dna`) 결과는 `{axis: 단일 값}` 형태 — 단일 값은
      AXIS_TYPES의 첫 요소 비율(예: color=high 비율)이라는 AI팀 확인을 따라
      `{type_a: v, type_b: 100-v}`로 펼친다.

    `100.0 - v` 같은 부동소수점 차연산은 `75.21000000000001` 같은 표현이 새므로
    응답에 노출되는 모든 값을 소수점 둘째 자리로 반올림한다.
    """
    if not raw:
        return None
    normalized: dict[str, object] = {}
    for axis, val in raw.items():
        if axis not in AXIS_TYPES:
            normalized[axis] = val
            continue
        type_a, type_b = AXIS_TYPES[axis]
        if isinstance(val, dict):
            normalized[axis] = {
                k: round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                for k, v in val.items()
            }
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            a = float(val)
            normalized[axis] = {type_a: round(a, 2), type_b: round(100.0 - a, 2)}
        else:
            normalized[axis] = val
    return normalized


@router.get("/me/space-dna", response_model=UserSpaceDNAResponse)
def get_my_space_dna(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """내 공간 DNA(3축: 자극 강도·분위기 밀도·트렌디함 + 누적 방문 수)를 반환합니다.

    아직 방문 체크인이 없거나 분석되지 않은 사용자는 `has_data=False`로 응답합니다.
    저장된 mbti_axes가 AI 트리거의 단일 값 형태든 온보딩의 중첩 dict 형태든
    응답에서는 항상 `{axis: {type_a: x, type_b: 100-x}}` 형태로 정규화됩니다.
    """
    dna = (
        db.query(UserSpaceDNA)
        .filter(UserSpaceDNA.user_id == current_user.id)
        .first()
    )
    if not dna or not dna.mbti_axes:
        return UserSpaceDNAResponse(
            has_data=False,
            total_visits=dna.total_visits if dna else 0,
            last_analyzed=dna.last_analyzed if dna else None,
        )
    return UserSpaceDNAResponse(
        has_data=True,
        mbti_axes=_normalize_axes_to_pairs(dna.mbti_axes),
        preferred_vibe_tags=dna.preferred_vibe_tags,
        total_visits=dna.total_visits,
        last_analyzed=dna.last_analyzed,
    )


@router.post(
    "/me/space-dna",
    response_model=UserSpaceDNAResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_my_space_dna(
    body: UserSpaceDNAOnboardingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """온보딩 16문항에서 프론트가 계산한 3축 비율(두 유형 dict)을 최초 1회 저장.

    이미 mbti_axes가 채워져 있으면 409. AI 자동 트리거가 먼저 만든 빈 행({})은
    UPDATE로 채워준다. WHERE mbti_axes='{}' + RETURNING으로 (a) 행 없음→INSERT,
    (b) 빈 행→UPDATE, (c) 채워진 행→no-op 세 분기를 단일 SQL로 원자 처리.
    """
    now = datetime.now(timezone.utc)
    axes = body.mbti_axes

    stmt = (
        pg_insert(UserSpaceDNA)
        .values(
            user_id=current_user.id,
            mbti_axes=axes,
            onboarding_axes=axes,  # 영구 시드 — rebuild가 덮어쓰지 않는 별도 컬럼
            total_visits=0,
            last_analyzed=now,
        )
        .on_conflict_do_update(
            index_elements=[UserSpaceDNA.user_id],
            set_={
                "mbti_axes": axes,
                "onboarding_axes": axes,
                "last_analyzed": now,
                # total_visits는 의도적으로 set_에서 제외해 기존 값을 보존한다
                # (AI 트리거가 빈 mbti_axes로 행 + 카운트를 먼저 만든 시나리오 방어).
            },
            where=UserSpaceDNA.mbti_axes == cast({}, JSONB),
        )
        .returning(UserSpaceDNA.user_id, UserSpaceDNA.total_visits)
    )
    row = db.execute(stmt).first()

    if row is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="이미 공간 DNA 온보딩이 완료되었습니다.",
        )

    db.commit()

    return UserSpaceDNAResponse(
        has_data=True,
        mbti_axes=axes,
        preferred_vibe_tags=None,
        total_visits=row.total_visits,
        last_analyzed=now,
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
