from typing import Optional

from pydantic import BaseModel


class PinResponse(BaseModel):
    """지도 마커 한 개 분량의 슬림 응답.

    상세(인스타 caption/메모/평점)는 클라이언트가 마커 클릭 시
    `GET /storages/{storage_id}/spots/{spot_id}`로 lazy 로딩한다.
    """

    spot_id: int
    storage_id: int
    place_id: int
    name: str
    latitude: float
    longitude: float
    is_visited: bool
    thumbnail_url: Optional[str] = None
