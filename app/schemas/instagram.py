from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

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
    # 게시물 캡션 (Apify는 전문, OG fallback은 일부)
    caption: str | None = None
    # 대표 이미지 URL 목록 (Apify는 다중 이미지, OG fallback은 og:image 1장)
    images: list[str] = Field(default_factory=list)
    # 장소명 (Apify의 locationName, OG fallback의 script JSON 추출)
    location_name: str | None = None
    # Instagram 위치 태그 고유 ID
    instagram_location_id: str | None = None
    # 위치 태그 좌표 (Apify에서만 제공)
    latitude: float | None = None
    longitude: float | None = None
    # 캡션의 해시태그/멘션 (Apify에서만 제공)
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    # 게시 시각 (Apify의 timestamp; ISO 문자열)
    posted_at: str | None = None
    # 작성자 (Apify의 ownerUsername)
    owner_username: str | None = None
    # OG 메타 원본(디버깅/품질 개선용; OG fallback일 때만 채워짐)
    og_title: str | None = None
    og_description: str | None = None


class InstagramSaveResponse(BaseModel):
    spot: SpotResponse
    already_saved: bool   # True = 이 storage에 이미 동일 장소 Spot이 존재했음
    place_created: bool   # True = 새 Place가 생성됨


class InstagramCrawlJobEnqueueResponse(BaseModel):
    """`/instagram/crawl-async` 응답.

    cache hit이면 result가 채워지고 job_id는 비어있다.
    miss면 job_id가 발급되고 result는 None이다."""
    job_id: str | None = None
    status: Literal["pending", "done"] = Field(
        ..., description="pending=잡 등록, done=캐시 hit 즉시 반환"
    )
    result: InstagramCrawlResponse | None = None


class InstagramJobStatusResponse(BaseModel):
    """`GET /instagram/jobs/{job_id}` 응답."""
    job_id: str
    status: Literal["pending", "done", "failed"]
    source: str | None = None  # 'apify' | 'og_fallback' | None(처리 전)
    result: InstagramCrawlResponse | None = None
    error: str | None = None


# ---------- /instagram/share (자동 매핑 + 저장) ----------

class InstagramShareRequest(BaseModel):
    url: HttpUrl = Field(..., description="인스타그램 게시물 URL")
    storage_id: int | None = Field(None, description="미제공 시 기본 저장소 자동 선택")


class InstagramCrawlData(BaseModel):
    """수동 폴백 시 클라이언트가 그대로 /instagram/save에 넘겨 쓰기 위한 슬림 모델."""
    url: HttpUrl
    caption: str | None = None
    thumbnail_url: str | None = None


class PlaceCandidate(BaseModel):
    """네이버 Local Search로 정규화된 장소 후보. /instagram/save의 NaverPlace 필드와 호환."""
    naver_place_id: str
    name: str
    address: str | None = None
    road_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    category: str | None = None
    category_group: str | None = None
    phone: str | None = None
    link: str | None = None
    raw_payload: dict | None = Field(None, description="네이버 Local Search 원본 item")


class InstagramShareResponse(BaseModel):
    """자동 매핑 흐름의 단일 응답 모델.

    status 분기:
    - "saved": 자동 저장 성공. spot/place_created/already_saved 사용.
    - "needs_selection": 후보가 2개 이상이라 사용자가 직접 골라야 함. crawl_data + candidates 사용.
    - "not_a_place_post": 네이버 매칭 결과 유니크 후보가 0개. 장소 게시물이 아니므로 저장 안 함.
      crawl_data만 채워짐(클라이언트에서 캡션·썸네일 미리보기 가능), candidates는 null.
    """
    model_config = ConfigDict(from_attributes=True)

    status: Literal["saved", "needs_selection", "not_a_place_post"]
    # saved 분기
    spot: SpotResponse | None = None
    already_saved: bool | None = None
    place_created: bool | None = None
    # needs_selection / not_a_place_post 분기
    crawl_data: InstagramCrawlData | None = None
    candidates: list[PlaceCandidate] | None = None
    # 디버깅 / 데이터 출처 표기
    crawl_source: str | None = None


# ---------- /instagram/share (비동기 큐 패턴) ----------

class InstagramShareEnqueueResponse(BaseModel):
    """`POST /instagram/share` 응답.

    - 캐시 hit: 즉시 처리됨. status="done", result에 InstagramShareResponse, job_id=None
    - 캐시 miss: 백그라운드 잡 등록. status="pending", job_id 발급, result=None
      → 클라이언트는 GET /instagram/share-jobs/{job_id}로 폴링
    """
    job_id: str | None = None
    status: Literal["pending", "done"] = Field(
        ..., description="pending=잡 등록, done=캐시 hit 즉시 처리"
    )
    result: InstagramShareResponse | None = None


class InstagramShareJobStatusResponse(BaseModel):
    """`GET /instagram/share-jobs/{job_id}` 응답."""
    job_id: str
    status: Literal["pending", "done", "failed"]
    result: InstagramShareResponse | None = None
    error: str | None = None
