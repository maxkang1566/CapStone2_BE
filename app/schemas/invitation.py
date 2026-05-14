from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class InvitationCreateRequest(BaseModel):
    role: Literal["editor", "viewer"]
    expires_in_days: int = Field(7, ge=1, le=30)


class InvitationResponse(BaseModel):
    """owner용 응답. 토큰 평문 노출 — 링크 공유용."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    token: str
    storage_id: int
    role: str
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    created_at: datetime
    invited_by_nickname: Optional[str] = None


class InvitationPreviewResponse(BaseModel):
    """수신자가 토큰 클릭 시 보는 정보. 멤버 가입 전 확인용."""
    storage_id: int
    storage_title: str
    role: str
    inviter_nickname: Optional[str] = None
    expires_at: datetime
