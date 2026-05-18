from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SpotCreate(BaseModel):
    place_id: int
    instagram_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    user_memo: Optional[str] = None
    user_rating: Optional[float] = None


class SpotUpdate(BaseModel):
    instagram_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    user_memo: Optional[str] = None
    user_rating: Optional[float] = None
    is_visited: Optional[bool] = None


class SpotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    storage_id: int
    place_id: int
    added_by: int
    instagram_url: Optional[str]
    thumbnail_url: Optional[str]
    caption: Optional[str]
    user_memo: Optional[str]
    user_rating: Optional[float]
    is_visited: bool
    visited_at: Optional[datetime]
    created_at: datetime
    deleted_at: Optional[datetime]


class NaverSpotCreateRequest(BaseModel):
    """앱 내 네이버지도 검색으로 선택한 장소를 storage에 Spot으로 저장하는 요청."""

    naver_place_id: str = Field(..., description="네이버 장소 ID")
    name: str = Field(..., description="장소명")
    address: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    category_group: Optional[str] = None
    phone: Optional[str] = None
    homepage_url: Optional[str] = None
    raw_payload: Optional[dict] = Field(None, description="네이버 SDK 원본 JSON")

    # 대표 이미지 — 없으면 후속 공간 DNA 분석 워커가 skip됨
    image_url: Optional[str] = Field(
        None, description="대표 이미지 URL (없으면 Space DNA 워커가 분석 skip)"
    )

    user_memo: Optional[str] = None
    user_rating: Optional[float] = None


class NaverSpotCreateResponse(BaseModel):
    spot: SpotResponse
    already_saved: bool   # True = 이 storage에 이미 동일 장소 Spot이 존재했음
    place_created: bool   # True = 새 Place가 생성됨
