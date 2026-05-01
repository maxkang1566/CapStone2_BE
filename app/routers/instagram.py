from fastapi import APIRouter, Depends, HTTPException, Request, status
import anyio
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Place, PlaceImage, PlaceRawData, Spot, StorageMember, User
from app.schemas.instagram import InstagramCrawlRequest, InstagramCrawlResponse, InstagramSaveRequest
from app.schemas.spot import SpotResponse
from app.services.instagram_crawler import InstagramCrawler
from app.services.playwright_manager import PlaywrightManager

router = APIRouter(prefix="/instagram", tags=["instagram"])


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


@router.post("/save", response_model=SpotResponse, status_code=status.HTTP_201_CREATED)
async def save_instagram_spot(
    body: InstagramSaveRequest,
    manager: PlaywrightManager = Depends(get_manager),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SpotResponse:
    """인스타그램 게시물을 크롤링해 Place → Spot으로 저장합니다.
    storage_id는 필수입니다.
    """
    # 창고 접근 권한 확인 (owner 또는 editor만 추가 가능)
    member = db.query(StorageMember).filter(
        StorageMember.storage_id == body.storage_id,
        StorageMember.user_id == current_user.id,
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="저장소를 찾을 수 없습니다.")
    if member.role not in ("owner", "editor"):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

    # 크롤링
    url_str = str(body.url)
    crawler = InstagramCrawler(manager=manager)
    try:
        result = await anyio.to_thread.run_sync(crawler.crawl_post, url_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e

    # OG 메타데이터가 비어있으면 비공개 계정이거나 삭제된 게시물
    if not result.og_title and not result.og_description and not result.images:
        raise HTTPException(
            status_code=404,
            detail="게시물을 불러올 수 없습니다. 비공개 계정이거나 삭제된 게시물일 수 있습니다.",
        )

    # Place 생성 (인스타 게시물마다 독립적인 장소 레코드 생성)
    place = Place(
        name=result.location_name or "이름 없음",
    )
    db.add(place)
    db.flush()  # place.id 확보

    # 원천 데이터 저장
    raw_data = PlaceRawData(
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
    )
    db.add(raw_data)
    db.flush()  # raw_data.id 확보

    # 이미지 저장
    for idx, image_url in enumerate(result.images):
        db.add(PlaceImage(
            place_id=place.id,
            raw_data_id=raw_data.id,
            image_url=image_url,
            source="instagram",
            is_representative=(idx == 0),
        ))

    # 같은 창고에 동일 장소 중복 저장 방지 (여기서는 URL로 중복 체크)
    existing = db.query(Spot).filter(
        Spot.storage_id == body.storage_id,
        Spot.instagram_url == url_str,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="이미 저장된 게시물입니다.")

    # Spot 생성
    thumbnail = result.images[0] if result.images else None
    spot = Spot(
        storage_id=body.storage_id,
        place_id=place.id,
        added_by=current_user.id,
        instagram_url=url_str,
        thumbnail_url=thumbnail,
        user_memo=result.caption,
    )
    db.add(spot)
    db.commit()
    db.refresh(spot)
    return spot
