from fastapi import APIRouter, Depends, HTTPException, Request, status
import anyio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Place, PlaceImage, PlaceRawData, Spot, Storage, StorageMember, User
from app.schemas.instagram import (
    InstagramCrawlRequest,
    InstagramCrawlResponse,
    InstagramSaveRequest,
    InstagramSaveResponse,
)
from app.services.instagram_crawler import InstagramCrawler
from app.services.playwright_manager import PlaywrightManager

router = APIRouter(prefix="/instagram", tags=["instagram"])


def _get_default_storage_id(user_id: int, db: Session) -> int:
    member = (
        db.query(StorageMember)
        .join(Storage, StorageMember.storage_id == Storage.id)
        .filter(
            StorageMember.user_id == user_id,
            StorageMember.role == "owner",
            Storage.deleted_at.is_(None),
        )
        .order_by(StorageMember.joined_at.asc())
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="기본 저장소를 찾을 수 없습니다.")
    return member.storage_id


def get_manager(request: Request) -> PlaywrightManager:
    manager = getattr(request.app.state, "playwright_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Playwright가 초기화되지 않았습니다.",
        )
    return manager


@router.post("/crawl", response_model=InstagramCrawlResponse)
async def crawl_instagram_post(
    body: InstagramCrawlRequest,
    manager: PlaywrightManager = Depends(get_manager),
) -> InstagramCrawlResponse:
    """인스타그램 게시물 URL을 받아 캡션/이미지 등을 크롤링합니다."""
    crawler = InstagramCrawler(manager=manager)
    try:
        result = await anyio.to_thread.run_sync(crawler.crawl_post, str(body.url))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(e)) from e

    if not result.og_title and not result.og_description and not result.images:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="게시물을 불러올 수 없습니다. 비공개 계정이거나 삭제된 게시물일 수 있습니다.",
        )
    return result


@router.post("/save", response_model=InstagramSaveResponse)
async def save_instagram_spot(
    body: InstagramSaveRequest,
    manager: PlaywrightManager = Depends(get_manager),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstagramSaveResponse:
    """인스타그램 게시물을 크롤링해 Place → Spot으로 저장합니다.
    - location_id가 있으면 같은 장소의 기존 Spot을 재사용합니다.
    - 이미 저장된 장소이면 already_saved=True와 기존 Spot을 반환합니다.
    - storage_id 미제공 시 기본 저장소에 자동 저장합니다.
    """
    storage_id = body.storage_id if body.storage_id is not None else _get_default_storage_id(current_user.id, db)

    # 창고 접근 권한 확인 (owner 또는 editor만 추가 가능)
    member = db.query(StorageMember).filter(
        StorageMember.storage_id == storage_id,
        StorageMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="저장소를 찾을 수 없습니다.")
    if member.role not in ("owner", "editor"):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

    # 같은 게시물 URL 중복 저장 방지
    url_str = str(body.url)
    if db.query(Spot).filter(Spot.storage_id == storage_id, Spot.instagram_url == url_str).first():
        raise HTTPException(status_code=409, detail="이미 저장된 게시물입니다.")

    # 크롤링
    crawler = InstagramCrawler(manager=manager)
    try:
        result = await anyio.to_thread.run_sync(crawler.crawl_post, url_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e

    if not result.og_title and not result.og_description and not result.images:
        raise HTTPException(
            status_code=404,
            detail="게시물을 불러올 수 없습니다. 비공개 계정이거나 삭제된 게시물일 수 있습니다.",
        )

    location_id = result.instagram_location_id
    place: Place
    place_created = False

    if location_id:
        # location_id 기반 upsert
        existing_raw = (
            db.query(PlaceRawData)
            .filter(
                PlaceRawData.provider == "instagram",
                PlaceRawData.provider_place_id == location_id,
            )
            .first()
        )
        if existing_raw:
            # 기존 Place 재사용 — 이 storage에 이미 같은 장소 Spot이 있는지 확인
            place = db.query(Place).filter(Place.id == existing_raw.place_id).first()
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
                return InstagramSaveResponse(
                    spot=existing_spot,
                    already_saved=True,
                    place_created=False,
                )
        else:
            # 새 Place + PlaceRawData 생성
            try:
                place = Place(name=result.location_name or "이름 없음")
                db.add(place)
                db.flush()
                db.add(PlaceRawData(
                    place_id=place.id,
                    provider="instagram",
                    provider_place_id=location_id,
                    raw_payload={
                        "url": url_str,
                        "caption": result.caption,
                        "og_title": result.og_title,
                        "og_description": result.og_description,
                        "images": result.images,
                        "location_name": result.location_name,
                        "instagram_location_id": location_id,
                    },
                ))
                db.flush()
                place_created = True
            except IntegrityError:
                db.rollback()
                existing_raw = (
                    db.query(PlaceRawData)
                    .filter(
                        PlaceRawData.provider == "instagram",
                        PlaceRawData.provider_place_id == location_id,
                    )
                    .first()
                )
                place = db.query(Place).filter(Place.id == existing_raw.place_id).first()
    else:
        # location_id 없음 — fallback: 항상 새 Place 생성
        place = Place(name=result.location_name or "이름 없음")
        db.add(place)
        db.flush()
        db.add(PlaceRawData(
            place_id=place.id,
            provider="instagram",
            raw_payload={
                "url": url_str,
                "caption": result.caption,
                "og_title": result.og_title,
                "og_description": result.og_description,
                "images": result.images,
                "location_name": result.location_name,
            },
        ))
        db.flush()
        place_created = True

    # 이미지 저장
    for idx, image_url in enumerate(result.images):
        db.add(PlaceImage(
            place_id=place.id,
            image_url=image_url,
            source="instagram",
            is_representative=(idx == 0),
        ))

    # Spot 생성
    thumbnail = result.images[0] if result.images else None
    spot = Spot(
        storage_id=storage_id,
        place_id=place.id,
        added_by=current_user.id,
        instagram_url=url_str,
        thumbnail_url=thumbnail,
        user_memo=result.caption,
    )
    db.add(spot)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 저장된 장소입니다.")
    db.refresh(spot)
    return InstagramSaveResponse(spot=spot, already_saved=False, place_created=place_created)
