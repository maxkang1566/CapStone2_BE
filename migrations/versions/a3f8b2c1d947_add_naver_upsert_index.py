"""add_naver_upsert_index

Revision ID: a3f8b2c1d947
Revises: 5fe6c16c6978
Create Date: 2026-05-02 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a3f8b2c1d947'
down_revision: Union[str, Sequence[str], None] = '5fe6c16c6978'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'uq_place_raw_data_provider_pid',
        'place_raw_data',
        ['provider', 'provider_place_id'],
        unique=True,
        postgresql_where=sa.text("provider_place_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_place_raw_data_provider_pid',
        table_name='place_raw_data',
        postgresql_where=sa.text("provider_place_id IS NOT NULL"),
    )
