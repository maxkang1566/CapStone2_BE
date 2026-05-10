from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class StorageCreate(BaseModel):
    title: str
    description: Optional[str] = None
    is_public: bool = False


class StorageUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None


class StorageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str]
    is_public: bool
    created_at: datetime
    deleted_at: Optional[datetime]


class StorageMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    storage_id: int
    user_id: int
    role: str
    joined_at: datetime


class StorageMemberAddRequest(BaseModel):
    user_id: int
    role: Literal["editor", "viewer"]


class StorageMemberRoleUpdate(BaseModel):
    role: Literal["owner", "editor", "viewer"]


class StorageMemberDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    storage_id: int
    user_id: int
    role: str
    joined_at: datetime
    nickname: Optional[str] = None
    profile_image: Optional[str] = None
