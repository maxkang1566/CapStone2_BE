"""인스타그램 공유 흐름의 Spot 생성 로직.

`/instagram/save`(수동 폴백)과 `/instagram/share`(자동 매핑 성공 분기) 양쪽이
같은 Place upsert + Spot 생성 절차를 거치므로 한 곳에 모은다.

절차:
    1. storage 접근 권한 확인 (owner/editor만 가능)
    2. 같은 storage에 동일 instagram_url Spot이 이미 있는지 확인 → 있으면 DuplicateInstagramUrlError
    3. naver_place_id 기준으로 Place upsert (PlaceRawData provider="naver")
    4. 같은 Place의 Spot이 이미 storage에 있으면 already_saved=True 반환 (insert 안 함)
    5. 인스타 raw 연결: shortcode가 있으면 instagram_pipeline.save_cache가 이미 적재한
       PlaceRawData(provider="instagram") 행의 place_id를 UPDATE로 채워 연결한다.
       shortcode가 없는 경우(수동 폴백)에는 기존처럼 축약본을 새 행으로 INSERT.
    6. PlaceImage(대표) + Spot(caption 포함) 생성 후 commit
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from geoalchemy2.elements import WKTElement
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import (
    Place,
    PlaceImage,
    PlaceRawData,
    Spot,
    Storage,
    StorageMember,
    User,
)
from app.services import image_storage


class SpotCreationError(Exception):
    """Spot 생성 중 발생하는 일반 예외."""


class StorageNotFoundError(SpotCreationError):
    """storage_id에 해당하는 storage가 없거나 사용자 멤버십이 없음."""


class StoragePermissionError(SpotCreationError):
    """사용자가 storage에 viewer 권한만 있어 Spot 추가 불가."""


class DuplicateInstagramUrlError(SpotCreationError):
    """같은 storage에 동일 instagram_url의 Spot이 이미 존재."""


@dataclass(frozen=True)
class NaverPlaceData:
    """Place 생성·조회에 필요한 네이버 장소 정보."""
    naver_place_id: str
    name: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    category_group: Optional[str] = None
    phone: Optional[str] = None
    homepage_url: Optional[str] = None
    raw_payload: Optional[dict] = None


@dataclass(frozen=True)
class InstagramData:
    """Spot에 첨부할 인스타그램 부가 데이터."""
    url: str
    # shortcode가 채워져 있으면 인스타 raw 행(provider='instagram', provider_place_id=shortcode)이
    # Apify 호출 시점에 이미 적재돼 있다고 가정하고, place_id를 UPDATE로 채워 연결한다.
    # None이면 수동 폴백 등으로 raw 적재 단계를 거치지 않은 케이스 → 기존처럼 새 행 INSERT.
    shortcode: Optional[str] = None
    caption: Optional[str] = None
    thumbnail_url: Optional[str] = None
    user_memo: Optional[str] = None
    user_rating: Optional[float] = None


@dataclass
class SpotCreateResult:
    spot: Spot
    place_created: bool = False
    already_saved: bool = False


def _check_storage_permission(storage_id: int, user_id: int, db: Session) -> StorageMember:
    member = (
        db.query(StorageMember)
        .filter(
            StorageMember.storage_id == storage_id,
            StorageMember.user_id == user_id,
        )
        .first()
    )
    if not member:
        raise StorageNotFoundError("저장소를 찾을 수 없습니다.")
    if member.role not in ("owner", "editor"):
        raise StoragePermissionError("접근 권한이 없습니다.")
    return member


def _upsert_naver_place(naver: NaverPlaceData, db: Session) -> tuple[Place, bool]:
    """naver_place_id 기준으로 Place + PlaceRawData(provider='naver')를 upsert한다.

    Returns: (place, created)
    """
    existing_raw = (
        db.query(PlaceRawData)
        .filter(
            PlaceRawData.provider == "naver",
            PlaceRawData.provider_place_id == naver.naver_place_id,
        )
        .first()
    )
    if existing_raw:
        place = db.query(Place).filter(Place.id == existing_raw.place_id).first()
        return place, False

    coordinate = None
    if naver.latitude is not None and naver.longitude is not None:
        coordinate = WKTElement(f"POINT({naver.longitude} {naver.latitude})", srid=4326)

    try:
        place = Place(
            name=naver.name,
            address=naver.address,
            coordinate=coordinate,
            category_group=naver.category_group,
            phone=naver.phone,
            homepage_url=naver.homepage_url,
        )
        db.add(place)
        db.flush()
        db.add(PlaceRawData(
            place_id=place.id,
            provider="naver",
            provider_place_id=naver.naver_place_id,
            raw_payload=naver.raw_payload,
        ))
        db.flush()
        return place, True
    except IntegrityError:
        # 동시성 충돌: 다른 트랜잭션이 같은 naver_place_id로 먼저 insert했다.
        db.rollback()
        existing_raw = (
            db.query(PlaceRawData)
            .filter(
                PlaceRawData.provider == "naver",
                PlaceRawData.provider_place_id == naver.naver_place_id,
            )
            .first()
        )
        place = db.query(Place).filter(Place.id == existing_raw.place_id).first()
        return place, False


def create_spot_from_naver_manual(
    naver: NaverPlaceData,
    storage_id: int,
    current_user: User,
    db: Session,
    *,
    image_url: Optional[str] = None,
    user_memo: Optional[str] = None,
    user_rating: Optional[float] = None,
) -> SpotCreateResult:
    """앱 내 네이버지도 검색으로 받은 장소를 Spot으로 저장한다.

    `create_spot_from_naver`(인스타 흐름)와 분리한 이유:
    - 인스타 흐름은 instagram_url 단위 중복 체크 + PlaceRawData(provider='instagram')
      연결 로직이 본질. 매뉴얼 저장은 이 둘 다 의미 없음(IG 메타가 아예 없으므로).
    - 분기 추가보다 새 함수가 의도가 명확하고 각자 단순.

    공유하는 부분: `_check_storage_permission` + `_upsert_naver_place`.

    image_url이 주어지면 Supabase Storage로 업로드해 영구 URL을 PlaceImage·
    Spot.thumbnail_url에 저장한다(업로드 실패 시 원본 URL로 폴백 — IG와 동일).
    이미지가 있어야 후속 공간 DNA 분석 워커가 동작한다(없으면 워커가 skip).
    """
    _check_storage_permission(storage_id, current_user.id, db)

    place, place_created = _upsert_naver_place(naver, db)

    # 같은 storage에 동일 Place Spot이 이미 있는지 (장소 단위 중복)
    existing_spot = (
        db.query(Spot)
        .filter(
            Spot.storage_id == storage_id,
            Spot.place_id == place.id,
            Spot.deleted_at.is_(None),
        )
        .first()
    )
    if existing_spot:
        return SpotCreateResult(spot=existing_spot, place_created=False, already_saved=True)

    saved_image_url: Optional[str] = None
    if image_url:
        permanent_url = image_storage.upload_naver_place_image(
            image_url=image_url,
            naver_place_id=naver.naver_place_id,
        )
        saved_image_url = permanent_url or image_url
        # uploaded_by는 IG 자동 크롤과 달리 매뉴얼 저장이라 사용자 의도가 명확 → 채워서
        # 누가 등록했는지 추적성을 살린다.
        db.add(PlaceImage(
            place_id=place.id,
            image_url=saved_image_url,
            source="naver",
            is_representative=True,
            uploaded_by=current_user.id,
        ))

    spot = Spot(
        storage_id=storage_id,
        place_id=place.id,
        added_by=current_user.id,
        instagram_url=None,
        thumbnail_url=saved_image_url,
        caption=None,
        user_memo=user_memo,
        user_rating=user_rating,
    )
    db.add(spot)
    try:
        db.commit()
    except IntegrityError as e:
        # 동시성: 같은 (storage_id, place_id)가 방금 다른 트랜잭션에 의해 INSERT됨,
        # 또는 soft-deleted 행이 unique 충돌 (uq_spots_storage_place는 partial 아님).
        db.rollback()
        raise SpotCreationError("이미 저장된 장소입니다.") from e
    db.refresh(spot)
    return SpotCreateResult(spot=spot, place_created=place_created, already_saved=False)


def create_spot_from_naver(
    naver: NaverPlaceData,
    instagram: InstagramData,
    storage_id: int,
    current_user: User,
    db: Session,
) -> SpotCreateResult:
    """네이버 장소 정보 + 인스타 데이터로 Spot을 생성한다."""
    _check_storage_permission(storage_id, current_user.id, db)

    # 같은 storage에 동일 instagram_url Spot이 있는지 확인 (게시물 단위 중복 방지)
    if db.query(Spot).filter(
        Spot.storage_id == storage_id,
        Spot.instagram_url == instagram.url,
    ).first():
        raise DuplicateInstagramUrlError("이미 저장된 게시물입니다.")

    place, place_created = _upsert_naver_place(naver, db)

    # 같은 storage에 동일 Place Spot이 이미 있는지 (장소 단위 중복: 다른 IG로 이미 저장됨)
    existing_spot = (
        db.query(Spot)
        .filter(
            Spot.storage_id == storage_id,
            Spot.place_id == place.id,
            Spot.deleted_at.is_(None),
        )
        .first()
    )
    if existing_spot:
        return SpotCreateResult(spot=existing_spot, place_created=False, already_saved=True)

    # 인스타그램 원본 raw 연결: shortcode가 있으면 이미 적재된 raw 행에 place_id를 채우고,
    # 없으면 (수동 폴백 등) 기존처럼 축약본을 새로 INSERT한다.
    if instagram.shortcode:
        # 조건부 UPDATE: place_id가 아직 비어있을 때만 연결. 이미 다른 트랜잭션이 다른
        # place_id로 채웠다면 rowcount=0이 되어 그대로 둔다(같은 게시물을 다른 storage가
        # 먼저 다른 가게로 매핑한 경우 — 비현실적이지만 race 안전).
        result = db.execute(
            sa_update(PlaceRawData)
            .where(
                PlaceRawData.provider == "instagram",
                PlaceRawData.provider_place_id == instagram.shortcode,
                PlaceRawData.place_id.is_(None),
            )
            .values(place_id=place.id)
        )
        if result.rowcount == 0:
            existing = (
                db.query(PlaceRawData)
                .filter(
                    PlaceRawData.provider == "instagram",
                    PlaceRawData.provider_place_id == instagram.shortcode,
                )
                .first()
            )
            if existing is None:
                # 캐시 누락 방어: 정규 흐름이라면 save_cache가 이미 적재했어야 함
                db.add(PlaceRawData(
                    place_id=place.id,
                    provider="instagram",
                    provider_place_id=instagram.shortcode,
                    raw_payload={
                        "url": instagram.url,
                        "caption": instagram.caption,
                        "thumbnail_url": instagram.thumbnail_url,
                    },
                ))
    else:
        db.add(PlaceRawData(
            place_id=place.id,
            provider="instagram",
            provider_place_id=None,
            raw_payload={
                "url": instagram.url,
                "caption": instagram.caption,
                "thumbnail_url": instagram.thumbnail_url,
            },
        ))

    # 대표 이미지 — 인스타 CDN URL은 4~5일 후 만료되므로 Supabase Storage에
    # 업로드 후 영구 URL을 저장한다. 업로드 실패 시 원본 URL로 폴백(가용성 우선).
    permanent_url: Optional[str] = None
    if instagram.thumbnail_url:
        permanent_url = image_storage.upload_instagram_image(
            image_url=instagram.thumbnail_url,
            shortcode=instagram.shortcode,
        )
        saved_image_url = permanent_url or instagram.thumbnail_url
        db.add(PlaceImage(
            place_id=place.id,
            image_url=saved_image_url,
            source="instagram",
            is_representative=True,
        ))
    else:
        saved_image_url = None

    spot = Spot(
        storage_id=storage_id,
        place_id=place.id,
        added_by=current_user.id,
        instagram_url=instagram.url,
        thumbnail_url=saved_image_url,
        caption=instagram.caption,
        user_memo=instagram.user_memo,
        user_rating=instagram.user_rating,
    )
    db.add(spot)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise DuplicateInstagramUrlError("이미 저장된 장소입니다.") from e
    db.refresh(spot)
    return SpotCreateResult(spot=spot, place_created=place_created, already_saved=False)
