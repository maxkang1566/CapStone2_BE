import os  # 환경변수(DATABASE_URL)를 읽기 위해 사용

from dotenv import load_dotenv  # .env 파일을 환경변수로 로드
from sqlalchemy import create_engine  # DB 엔진(연결)을 생성
from sqlalchemy.orm import declarative_base, sessionmaker  # 모델 Base/세션 팩토리 생성

# 프로젝트 루트의 .env를 읽어서 DATABASE_URL 같은 값을 환경변수로 올립니다.
load_dotenv()

# DB 접속 주소(예: postgresql://user:password@localhost:5432/picklog)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경변수가 설정되어 있지 않습니다. (.env 확인 필요)")

# 1) Engine: 실제 DB 서버와 연결을 담당하는 '엔진(트럭 엔진)'입니다.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# 2) SessionLocal: 요청마다 DB 세션을 빌려주기 위한 '세션 공장'입니다.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 3) Base: SQLAlchemy 모델(테이블)을 선언할 때 상속받는 베이스 클래스입니다.
Base = declarative_base()


def get_db():
    """API 요청 처리 동안 사용할 DB 세션을 제공하고, 끝나면 닫습니다."""

    # 요청이 들어올 때 DB 세션을 하나 엽니다.
    db = SessionLocal()
    try:
        # 라우터에서 Depends(get_db)로 주입받아 사용합니다.
        yield db
    finally:
        # 요청이 끝나면 세션을 닫아 커넥션을 반환합니다.
        db.close()

