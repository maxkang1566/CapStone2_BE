"""extend_instagram_jobs_for_share

Revision ID: c8d3e5f1a934
Revises: b7c4d2e9a812
Create Date: 2026-05-07 12:00:00.000000

instagram_crawl_jobs 테이블에 share 잡을 함께 저장하기 위한 컬럼을 추가한다.
- kind: 'crawl' | 'share' (기존 행은 모두 'crawl'로 백필)
- user_id, storage_id: share 잡 한정. 워커가 share_post를 재구성할 컨텍스트
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c8d3e5f1a934"
down_revision: Union[str, Sequence[str], None] = "b7c4d2e9a812"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # kind는 server_default='crawl'로 추가해 기존 행 자동 백필 후 NOT NULL 적용
    op.add_column(
        "instagram_crawl_jobs",
        sa.Column("kind", sa.String(), nullable=False, server_default="crawl"),
    )

    op.add_column(
        "instagram_crawl_jobs",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "instagram_crawl_jobs",
        sa.Column("storage_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_instagram_crawl_jobs_user",
        "instagram_crawl_jobs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_instagram_crawl_jobs_storage",
        "instagram_crawl_jobs",
        "storages",
        ["storage_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_instagram_crawl_jobs_kind_created",
        "instagram_crawl_jobs",
        ["kind", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_instagram_crawl_jobs_kind_created",
        table_name="instagram_crawl_jobs",
    )
    op.drop_constraint(
        "fk_instagram_crawl_jobs_storage",
        "instagram_crawl_jobs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_instagram_crawl_jobs_user",
        "instagram_crawl_jobs",
        type_="foreignkey",
    )
    op.drop_column("instagram_crawl_jobs", "storage_id")
    op.drop_column("instagram_crawl_jobs", "user_id")
    op.drop_column("instagram_crawl_jobs", "kind")
