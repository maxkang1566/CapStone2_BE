from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


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
    user_memo: Optional[str]
    user_rating: Optional[float]
    is_visited: bool
    visited_at: Optional[datetime]
    created_at: datetime
    deleted_at: Optional[datetime]
