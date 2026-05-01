from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.models import Storage, StorageMember, User
from app.schemas.user import Token, UserCreate, UserResponse

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
