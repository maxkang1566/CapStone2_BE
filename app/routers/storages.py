import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.queue import get_rq_queue
from app.models.models import Storage, StorageMember, User
from app.schemas.spot import (
    NaverSpotCreateRequest,
    NaverSpotCreateResponse,
)
from app.schemas.storage import (
    StorageCreate,
    StorageMemberAddRequest,
    StorageMemberDetailResponse,
    StorageMemberRoleUpdate,
    StorageResponse,
    StorageUpdate,
)
from app.services import place_enrichment
from app.services.space_dna_analyzer import enqueue_space_dna_analysis
from app.services.spot_creator import (
    NaverPlaceData,
    SpotCreationError,
    StorageNotFoundError,
    StoragePermissionError,
    create_spot_from_naver_manual,
)

logger = logging.getLogger(__name__)

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
    page: int = Query(1, ge=1, description="페이지 번호"),
    size: int = Query(20, ge=1, le=100, description="페이지당 항목 수"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """현재 유저가 멤버로 속한 창고 목록을 반환합니다 (소프트 삭제된 창고 제외)."""
    return (
        db.query(Storage)
        .join(StorageMember, Storage.id == StorageMember.storage_id)
        .filter(
            StorageMember.user_id == current_user.id,
            Storage.deleted_at.is_(None),
        )
        .offset((page - 1) * size)
        .limit(size)
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


# ----- 멤버 관리 -----

def _get_target_member(
    storage_id: int, target_user_id: int, db: Session
) -> StorageMember:
    target = (
        db.query(StorageMember)
        .filter(
            StorageMember.storage_id == storage_id,
            StorageMember.user_id == target_user_id,
        )
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="멤버를 찾을 수 없습니다.")
    return target


def _to_member_detail(member: StorageMember) -> StorageMemberDetailResponse:
    user = member.user
    return StorageMemberDetailResponse(
        storage_id=member.storage_id,
        user_id=member.user_id,
        role=member.role,
        joined_at=member.joined_at,
        nickname=user.nickname if user else None,
        profile_image=user.profile_image if user else None,
    )


@router.get(
    "/{storage_id}/members",
    response_model=list[StorageMemberDetailResponse],
)
def list_members(
    storage_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """창고 멤버 목록. 멤버 누구나 조회 가능."""
    _get_member(storage_id, db, current_user)
    members = (
        db.query(StorageMember)
        .options(joinedload(StorageMember.user))
        .filter(StorageMember.storage_id == storage_id)
        .order_by(StorageMember.joined_at.asc())
        .all()
    )
    return [_to_member_detail(m) for m in members]


@router.post(
    "/{storage_id}/members",
    response_model=StorageMemberDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    storage_id: int,
    body: StorageMemberAddRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """user_id로 멤버 추가 (owner 전용). role은 editor 또는 viewer만 허용."""
    _get_member(storage_id, db, current_user, required_roles=("owner",))

    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    existing = (
        db.query(StorageMember)
        .filter(
            StorageMember.storage_id == storage_id,
            StorageMember.user_id == body.user_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="이미 저장소 멤버입니다.")

    new_member = StorageMember(
        storage_id=storage_id, user_id=body.user_id, role=body.role
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)
    return _to_member_detail(new_member)


@router.patch(
    "/{storage_id}/members/{user_id}",
    response_model=StorageMemberDetailResponse,
)
def update_member_role(
    storage_id: int,
    user_id: int,
    body: StorageMemberRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """멤버 role 변경 (owner 전용).

    role="owner"로 변경 시 기존 owner는 자동으로 editor로 강등 — 같은 트랜잭션
    안에서 두 UPDATE가 단일 commit으로 처리되므로 외부에서 owner 0명/2명
    상태를 관측할 수 없다. 동시 transfer 레이스는 owner 1명 모델상 실질 불가하여
    row-lock은 두지 않는다.
    """
    caller = _get_member(storage_id, db, current_user, required_roles=("owner",))
    target = _get_target_member(storage_id, user_id, db)

    # 자기 자신을 owner로 다시 지정 → 멱등 no-op
    if target.user_id == caller.user_id and body.role == "owner":
        return _to_member_detail(target)

    # 유일 owner 본인 강등 거부
    if target.user_id == caller.user_id and body.role != "owner":
        raise HTTPException(
            status_code=409,
            detail="유일한 소유자는 강등할 수 없습니다. 먼저 다른 멤버에게 소유권을 이전하세요.",
        )

    if body.role == "owner":
        caller.role = "editor"
        target.role = "owner"
    else:
        target.role = body.role
    db.commit()
    db.refresh(target)
    return _to_member_detail(target)


# /members/me는 반드시 /members/{user_id}보다 먼저 선언해야 라우트 매칭이
# 정상 동작한다 (FastAPI는 등록 순서대로 매칭).
@router.delete(
    "/{storage_id}/members/me",
    status_code=status.HTTP_204_NO_CONTENT,
)
def leave_storage(
    storage_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인이 저장소에서 나가기. owner는 leave 불가 (transfer 또는 storage 삭제)."""
    member = _get_member(storage_id, db, current_user)
    if member.role == "owner":
        raise HTTPException(
            status_code=400,
            detail="소유자는 떠날 수 없습니다. 먼저 소유권을 이전하거나 저장소를 삭제하세요.",
        )
    db.delete(member)
    db.commit()


@router.delete(
    "/{storage_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_member(
    storage_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """멤버 추방 (owner 전용). 본인은 /members/me로 떠나야 한다."""
    _get_member(storage_id, db, current_user, required_roles=("owner",))
    if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="본인은 추방할 수 없습니다. /members/me 엔드포인트로 떠나세요.",
        )
    target = _get_target_member(storage_id, user_id, db)
    db.delete(target)
    db.commit()


# ----- 네이버지도 직접 저장 (IG 흐름과 동등한 사이드이펙트) -----


@router.post(
    "/{storage_id}/spots/from-naver",
    response_model=NaverSpotCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_spot_from_naver_endpoint(
    storage_id: int,
    body: NaverSpotCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """앱 내 네이버지도 검색으로 선택한 장소를 storage에 Spot으로 저장한다.

    IG 공유 흐름과 동등한 사이드이펙트:
      1. Place upsert (naver_place_id 기준)
      2. PlaceImage 저장 (image_url 있을 때 Supabase 업로드)
      3. Spot 생성
      4. 네이버 블로그 리뷰 수집 잡 enqueue (RQ, 멱등)
      5. 공간 DNA 분석 잡 enqueue (RQ, 멱등)

    이미 같은 (storage, place) Spot이 있으면 already_saved=True로 반환하고
    enqueue는 그대로 호출(워커가 fresh/analyzed 가드로 skip하므로 안전).
    """
    naver = NaverPlaceData(
        naver_place_id=body.naver_place_id,
        name=body.name,
        address=body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        category_group=body.category_group,
        phone=body.phone,
        homepage_url=body.homepage_url,
        raw_payload=body.raw_payload,
    )

    try:
        result = create_spot_from_naver_manual(
            naver,
            storage_id,
            current_user,
            db,
            image_url=body.image_url,
            user_memo=body.user_memo,
            user_rating=body.user_rating,
        )
    except StorageNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except StoragePermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except SpotCreationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 후속 잡 enqueue — best-effort. 큐 미초기화/Redis 다운이어도 본 응답엔 영향 없음.
    # 두 enqueue는 워커 단 멱등 가드(_should_refresh, _already_analyzed)가 있어서
    # already_saved=True 케이스에도 그대로 호출해도 안전.
    try:
        rq_queue = get_rq_queue(request)
        place_enrichment.enqueue_blog_fetch_job(
            place_id=result.spot.place_id,
            user_id=current_user.id,
            queue=rq_queue,
            db=db,
        )
        enqueue_space_dna_analysis(result.spot.place_id, rq_queue)
    except HTTPException:
        logger.exception(
            "후속 잡 enqueue 실패(큐 미초기화): place_id=%s",
            result.spot.place_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "후속 잡 enqueue 실패: place_id=%s",
            result.spot.place_id,
        )

    return NaverSpotCreateResponse(
        spot=result.spot,
        already_saved=result.already_saved,
        place_created=result.place_created,
    )
