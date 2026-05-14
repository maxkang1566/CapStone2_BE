"""add_storage_invitations

Revision ID: e6b8d2f0a3c5
Revises: d4f9a1b3c5e7
Create Date: 2026-05-11 15:00:00.000000

창고 멤버 토큰 초대 링크 기능을 위한 storage_invitations 테이블 신설.
멀티유저 공유 모델: 토큰 1개를 여러 사용자가 가입에 사용 가능 (만료/취소까지).
동일 사용자 중복 가입 차단은 기존 uq_storage_members_storage_user가 자동 처리.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6b8d2f0a3c5"
down_revision: Union[str, Sequence[str], None] = "d4f9a1b3c5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "storage_invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("storage_id", sa.Integer(), nullable=False),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["storage_id"], ["storages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_storage_invitations_token"),
    )
    op.create_index(
        "ix_storage_invitations_storage_id",
        "storage_invitations",
        ["storage_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_storage_invitations_storage_id", table_name="storage_invitations"
    )
    op.drop_table("storage_invitations")
