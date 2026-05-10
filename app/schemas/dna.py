from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class PlaceSpaceDNAResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    has_data: bool
    mbti_axes: Optional[dict] = None
    ai_summary: Optional[str] = None
    updated_at: Optional[datetime] = None


class UserSpaceDNAResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    has_data: bool
    mbti_axes: Optional[dict] = None
    preferred_vibe_tags: Optional[dict] = None
    total_visits: int = 0
    last_analyzed: Optional[datetime] = None
