from fastapi import APIRouter, HTTPException  # 간단한 상태 확인 API를 만들기 위한 라우터
from sqlalchemy import text  # DB에 가벼운 쿼리를 날리기 위한 SQL 텍스트
from sqlalchemy.orm import Session  # SQLAlchemy 세션 타입
from sqlalchemy.exc import OperationalError

from app.core.database import get_db  # 요청마다 DB 세션을 주입받기 위한 의존성
from fastapi import Depends  # DI(Depends)를 사용

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/db")
def health_db(db: Session = Depends(get_db)):
    # DB 연결이 살아있는지 확인하기 위해 아주 가벼운 쿼리(SELECT 1)를 실행합니다.
    try:
        db.execute(text("SELECT 1"))
    except OperationalError as e:
        raise HTTPException(status_code=503, detail="DB 연결 불가") from e
    return {"status": "ok", "db": "connected"}

