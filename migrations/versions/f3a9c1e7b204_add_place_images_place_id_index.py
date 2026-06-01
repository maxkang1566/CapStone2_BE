"""add_place_images_place_id_index

Revision ID: f3a9c1e7b204
Revises: e6b8d2f0a3c5
Create Date: 2026-06-01 15:20:00.000000

place_images.place_id에 인덱스를 추가한다.
- 테이블 생성(8d6bab21bc7a) 시 FK 제약만 걸리고 인덱스는 없어, 장소별 이미지 조회
  (GET /places/{id}/images, space_dna_analyzer._pick_image_urls)가 seq scan을 탄다.
- Postgres는 FK 참조 컬럼에 인덱스를 자동 생성하지 않으므로 명시 선언이 필요하다.
- place_reviews의 ix_place_reviews_place_id(5fe6c16c6978)와 동일 패턴.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f3a9c1e7b204"
down_revision: Union[str, Sequence[str], None] = "e6b8d2f0a3c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_place_images_place_id", "place_images", ["place_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_place_images_place_id", table_name="place_images")
