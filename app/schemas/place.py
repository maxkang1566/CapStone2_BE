from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


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
    coordinate: Optional[str]
    category_group: Optional[str]
    phone: Optional[str]
    homepage_url: Optional[str]
    created_at: datetime
