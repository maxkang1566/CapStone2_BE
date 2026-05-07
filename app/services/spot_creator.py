"""인스타그램 공유 흐름의 Spot 생성 로직.

`/instagram/save`(수동 폴백)과 `/instagram/share`(자동 매핑 성공 분기) 양쪽이
같은 Place upsert + Spot 생성 절차를 거치므로 한 곳에 모은다.

절차:
    1. storage 접근 권한 확인 (owner/editor만 가능)
    2. 같은 storage에 동일 instagram_url Spot이 이미 있는지 확인 → 있으면 DuplicateInstagramUrlError
    3. naver_place_id 기준으로 Place upsert (PlaceRawData provider="naver")
    4. 같은 Place의 Spot이 이미 storage에 있으면 already_saved=True 반환 (insert 안 함)
    5. PlaceRawData(provider="instagram") + PlaceImage(대표) + Spot 생성 후 commit
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from geoalchemy2.elements import WKTElement
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

    # 인스타그램 원본 보관
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

    # 대표 이미지
    if instagram.thumbnail_url:
        db.add(PlaceImage(
            place_id=place.id,
            image_url=instagram.thumbnail_url,
            source="instagram",
            is_representative=True,
        ))

    spot = Spot(
        storage_id=storage_id,
        place_id=place.id,
        added_by=current_user.id,
        instagram_url=instagram.url,
        thumbnail_url=instagram.thumbnail_url,
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
