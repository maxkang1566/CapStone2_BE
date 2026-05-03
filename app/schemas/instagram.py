from pydantic import BaseModel, HttpUrl, Field

from app.schemas.spot import SpotResponse


class InstagramCrawlRequest(BaseModel):
    # 크롤링 대상 게시물 URL
    url: HttpUrl = Field(..., description="인스타그램 게시물 URL")


class InstagramSaveRequest(BaseModel):
    url: HttpUrl = Field(..., description="인스타그램 게시물 URL")
    storage_id: int | None = Field(None, description="저장할 저장소 ID (미제공 시 기본 저장소에 자동 저장)")


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
