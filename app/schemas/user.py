from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    nickname: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    nickname: Optional[str] = None
    profile_image: Optional[str] = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nickname: Optional[str]
    profile_image: Optional[str]
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class KakaoLoginRequest(BaseModel):
    access_token: str  # 모바일 카카오 SDK에서 받은 access_token


class KakaoLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool
