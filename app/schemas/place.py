from datetime import datetime
from typing import Any, Optional

from geoalchemy2.shape import to_shape
from pydantic import BaseModel, ConfigDict, Field, computed_field


class PlaceCreate(BaseModel):
    name: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    category_group: Optional[str] = None
    phone: Optional[str] = None
    homepage_url: Optional[str] = None


class PlaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: Optional[str]
    coordinate: Optional[Any] = Field(default=None, exclude=True)
    category_group: Optional[str]
    phone: Optional[str]
    homepage_url: Optional[str]
    created_at: datetime

    @computed_field
    @property
    def latitude(self) -> Optional[float]:
        if self.coordinate is None:
            return None
        return to_shape(self.coordinate).y

    @computed_field
    @property
    def longitude(self) -> Optional[float]:
        if self.coordinate is None:
            return None
        return to_shape(self.coordinate).x


class PlaceRawDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    place_id: int
    provider: Optional[str]
    provider_place_id: Optional[str]
    raw_payload: Optional[dict]
    collected_at: datetime


class PlaceReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    place_id: int
    raw_data_id: Optional[int]
    provider: Optional[str]
    external_review_id: Optional[str]
    rating: Optional[float]
    text: Optional[str]
    reviewed_at: Optional[datetime]
    collected_at: datetime
