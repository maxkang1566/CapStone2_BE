from pydantic import BaseModel, HttpUrl, Field

from app.schemas.spot import SpotResponse


class InstagramCrawlRequest(BaseModel):
    # 크롤링 대상 게시물 URL
    url: HttpUrl = Field(..., description="인스타그램 게시물 URL")


class InstagramSaveRequest(BaseModel):
    # Instagram 게시물 정보 (클라이언트가 /crawl 결과에서 전달)
    instagram_url: HttpUrl = Field(..., description="인스타그램 게시물 URL")
    caption: str | None = Field(None, description="게시물 캡션")
    thumbnail_url: str | None = Field(None, description="대표 이미지 URL")

    # 네이버 장소 정보 (사용자가 지도에서 선택 — 필수)
    naver_place_id: str = Field(..., description="네이버 장소 ID")
    place_name: str = Field(..., description="장소명")
    place_address: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    category_group: str | None = None
    place_raw_payload: dict | None = Field(None, description="네이버 SDK 원본 JSON")

    # 스팟 메타데이터
    storage_id: int | None = Field(None, description="미제공 시 기본 저장소 자동 선택")
    user_memo: str | None = None
    user_rating: float | None = None


class InstagramCrawlResponse(BaseModel):
    # 요청으로 받은 URL(정규화된 형태)
    url: HttpUrl
    # 게시물 캡션(가능하면 OG description 기반 추출)
    caption: str | None = None
    # 대표 이미지 URL 목록(현재는 OG image 위주)
    images: list[str] = Field(default_factory=list)
    # 장소명 힌트(있으면 script 태그 또는 OG description에서 추정)
    location_name: str | None = None
    # Instagram 위치 태그 고유 ID (script 태그에서 추출, 없으면 None)
    instagram_location_id: str | None = None
    # OG 메타 원본(디버깅/품질 개선용)
    og_title: str | None = None
    og_description: str | None = None


class InstagramSaveResponse(BaseModel):
    spot: SpotResponse
    already_saved: bool   # True = 이 storage에 이미 동일 장소 Spot이 존재했음
    place_created: bool   # True = 새 Place가 생성됨
