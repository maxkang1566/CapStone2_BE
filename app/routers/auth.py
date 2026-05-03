from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.models import Storage, StorageMember, User
from app.schemas.user import (
    KakaoLoginRequest,
    KakaoLoginResponse,
    Token,
    UserCreate,
    UserResponse,
)
from app.services.kakao_oauth import fetch_kakao_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(body: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="이미 사용 중인 이메일입니다.")

    user = User(
        email=body.email,
        password=hash_password(body.password),
        nickname=body.nickname,
    )
    db.add(user)
    db.flush()  # user.id를 확보하기 위해 flush (커밋 전)

    # 회원가입 시 기본 창고 자동 생성 + 소유자 멤버 등록
    storage = Storage(title="내 저장소", is_public=False)
    db.add(storage)
    db.flush()  # storage.id 확보

    member = StorageMember(storage_id=storage.id, user_id=user.id, role="owner")
    db.add(member)

    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2 표준은 username 필드를 사용하며, 여기서는 email로 처리합니다.
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not user.password or not verify_password(form.password, user.password):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token)


@router.post("/kakao", response_model=KakaoLoginResponse)
async def login_kakao(body: KakaoLoginRequest, db: Session = Depends(get_db)):
    """
    카카오 OAuth 로그인.

    모바일 앱이 카카오 SDK로 받은 access_token을 전달하면,
    백엔드가 카카오 사용자 정보를 조회해 자체 JWT를 발급합니다.

    매칭 우선순위:
      1) kakao_id로 기존 사용자 조회
      2) 없으면 email로 조회 → 있으면 kakao_id 연결 (계정 병합)
      3) 둘 다 없으면 신규 가입 (기본 저장소 자동 생성)
    """
    profile = await fetch_kakao_user(body.access_token)

    kakao_id = str(profile["id"])
    kakao_account = profile.get("kakao_account") or {}
    email = kakao_account.get("email")
    kakao_profile = kakao_account.get("profile") or {}
    nickname = kakao_profile.get("nickname")
    profile_image = kakao_profile.get("profile_image_url")

    is_new_user = False

    # 1) kakao_id 매칭
    user = db.query(User).filter(User.kakao_id == kakao_id).first()

    if not user:
        # 2) email 매칭 → kakao_id 연결 (계정 병합)
        if email:
            user = db.query(User).filter(User.email == email).first()
            if user:
                user.kakao_id = kakao_id
                db.commit()
                db.refresh(user)

        # 3) 신규 가입
        if not user:
            final_email = email or f"kakao_{kakao_id}@picklog.local"
            user = User(
                email=final_email,
                kakao_id=kakao_id,
                nickname=nickname,
                profile_image=profile_image,
            )
            db.add(user)
            db.flush()  # user.id 확보

            # 기본 저장소 자동 생성 + 소유자 멤버 등록
            storage = Storage(title="내 저장소", is_public=False)
            db.add(storage)
            db.flush()

            member = StorageMember(storage_id=storage.id, user_id=user.id, role="owner")
            db.add(member)

            db.commit()
            db.refresh(user)
            is_new_user = True

    token = create_access_token({"sub": str(user.id)})
    return KakaoLoginResponse(access_token=token, is_new_user=is_new_user)
