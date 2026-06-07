"""add_onboarding_axes_to_user_space_dna

Revision ID: b2e8f4a1c739
Revises: f3a9c1e7b204
Create Date: 2026-06-07 12:00:00.000000

user_space_dna에 onboarding_axes(JSONB, nullable) 컬럼을 추가한다.

- 온보딩 설문값을 영구 보관하는 별도 컬럼. rebuild_user_dna가 덮어쓰는 mbti_axes와
  분리해, 설문 DNA를 평균 풀의 동등 1표로 매 rebuild마다 영구 반영(옵션 1)하기 위함.
- 기존 옵션 2a는 첫 rebuild에만 mbti_axes 안의 온보딩을 섞고 그 rebuild가 mbti_axes를
  덮어써 원본 설문이 소실됐다.
- nullable: 온보딩 안 한 유저(NULL) vs 완료(값) 구분. server_default를 주지 않아
  "빈 설문"과 "설문 안 함"을 구별한다(mbti_axes의 server_default='{}'와 의도적 차이).

백필:
- total_visits=0 AND mbti_axes != '{}'인 유저는 아직 첫 rebuild 전이라 mbti_axes에
  원본 설문이 그대로 남아 있다 → onboarding_axes로 복사.
- total_visits>0인 유저는 옛 옵션 2a rebuild가 mbti_axes를 평균으로 이미 덮어써
  원본 설문이 소실됨 → 복구 불가(WHERE total_visits=0 가드로 잘못된 평균값을
  설문으로 오인 복사하는 것을 차단). 운영 데이터 적재 직전 단계라 영향 미미.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2e8f4a1c739"
down_revision: Union[str, Sequence[str], None] = "f3a9c1e7b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "user_space_dna",
        sa.Column(
            "onboarding_axes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    # 복구 가능 코호트만 백필: 첫 rebuild 전(total_visits=0)이라 mbti_axes에
    # 원본 설문이 남아 있는 행. total_visits>0은 이미 평균으로 덮여 복구 불가이므로 제외.
    op.execute(
        sa.text(
            "UPDATE user_space_dna "
            "SET onboarding_axes = mbti_axes "
            "WHERE total_visits = 0 AND mbti_axes <> '{}'::jsonb"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user_space_dna", "onboarding_axes")
