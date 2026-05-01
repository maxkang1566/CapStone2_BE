import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# .env 파일을 읽어 DATABASE_URL 등 환경변수를 로드합니다.
load_dotenv()

# Alembic Config 객체 — alembic.ini 값에 접근할 때 사용합니다.
config = context.config

# alembic.ini의 sqlalchemy.url 대신 .env의 DATABASE_URL을 사용합니다.
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

# Python 로깅 설정
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate가 인식할 수 있도록 Base.metadata를 등록합니다.
from app.core.database import Base  # noqa: E402
import app.models.models  # noqa: E402, F401 — 모든 모델을 Base.metadata에 등록

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """DB에 이미 존재하는 테이블 중 우리 모델에 없는 것(PostGIS 내장 테이블 등)은
    autogenerate 비교 대상에서 제외합니다."""
    if type_ == "table" and reflected and compare_to is None:
        return False
    return True


def run_migrations_offline() -> None:
    """오프라인 모드: DB 연결 없이 SQL 스크립트만 생성합니다."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """온라인 모드: 실제 DB에 연결해 마이그레이션을 적용합니다."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
