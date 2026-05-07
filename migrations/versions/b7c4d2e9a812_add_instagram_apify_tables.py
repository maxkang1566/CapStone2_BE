"""add_instagram_apify_tables

Revision ID: b7c4d2e9a812
Revises: a3f8b2c1d947
Create Date: 2026-05-06 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = 'b7c4d2e9a812'
down_revision: Union[str, Sequence[str], None] = 'a3f8b2c1d947'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'instagram_post_cache',
        sa.Column('shortcode', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('shortcode'),
    )

    op.create_table(
        'instagram_crawl_jobs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('shortcode', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_instagram_crawl_jobs_created_at',
        'instagram_crawl_jobs',
        ['created_at'],
        unique=False,
    )
    op.create_index(
        'ix_instagram_crawl_jobs_source_created',
        'instagram_crawl_jobs',
        ['source', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_instagram_crawl_jobs_source_created', table_name='instagram_crawl_jobs')
    op.drop_index('ix_instagram_crawl_jobs_created_at', table_name='instagram_crawl_jobs')
    op.drop_table('instagram_crawl_jobs')
    op.drop_table('instagram_post_cache')
