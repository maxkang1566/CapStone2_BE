"""unify_instagram_raw_into_place_raw_data

Revision ID: d4f9a1b3c5e7
Revises: c8d3e5f1a934
Create Date: 2026-05-08 10:00:00.000000

인스타 raw 데이터를 instagram_post_cache 테이블에서 place_raw_data로 통합한다.
사용자에게 보여줄 caption을 spots.caption 컬럼으로 정제 노출한다.

변경 사항:
  1. place_raw_data.place_id를 nullable로 변경 (raw 먼저 적재, 정제 단계에서 UPDATE로 채움)
  2. spots.caption TEXT NULL 컬럼 추가 (응답 SpotResponse에 노출)
  3. 기존 place_raw_data(provider='instagram', provider_place_id=NULL) 축약본 행의
     provider_place_id를 raw_payload->'url'에서 추출한 shortcode로 정정
     (단일 후보일 때만 — 부분 유니크 인덱스 충돌 방지용 NOT EXISTS 가드)
  4. instagram_post_cache 데이터를 place_raw_data로 복사 (메타키 _source/_url 주입,
     ON CONFLICT 시 raw_payload/collected_at만 갱신, place_id는 보존)
  5. 정정 가드를 통과하지 못한 NULL provider_place_id 축약본은 안전하게 삭제
  6. 기존 spots에 caption 백필 (raw_payload->'caption'에서)
  7. instagram_post_cache 테이블 DROP

⚠️ 실행 전 주의:
  - RQ 워커를 중지한 뒤 마이그레이션을 실행해야 한다. 실행 중이면 워커가 instagram_post_cache에
    INSERT하는 동시에 마이그레이션이 DROP을 시도해 race가 발생할 수 있다.

⚠️ Downgrade는 비파괴적이지 않다:
  - spots.caption 컬럼이 DROP되면서 백필된 caption 데이터가 손실된다.
  - place_id가 NULL인 place_raw_data 행은 NOT NULL 제약 복원을 위해 DELETE된다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4f9a1b3c5e7"
down_revision: Union[str, Sequence[str], None] = "c8d3e5f1a934"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 스키마 변경
    op.alter_column(
        "place_raw_data",
        "place_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "spots",
        sa.Column("caption", sa.Text(), nullable=True),
    )

    # 2) 기존 축약본 행의 provider_place_id를 shortcode로 정정.
    #    단일 후보일 때만 UPDATE — 같은 shortcode를 가진 NULL 행이 다중이면 부분 유니크 인덱스
    #    `uq_place_raw_data_provider_pid`(provider, provider_place_id) WHERE provider_place_id
    #    IS NOT NULL 충돌을 일으키므로 그대로 둔다(아래 5단계에서 정리).
    # SQLAlchemy text()가 `:name` 패턴을 bind parameter로 인식하므로 정규식의 `(?:...)`
    # 비-캡처 그룹은 사용할 수 없다(`:p`로 해석됨). 대신 regexp_match로 capturing group을
    # 명시하고 [2]번째(shortcode)를 추출한다.
    op.execute(
        sa.text(
            r"""
            WITH candidates AS (
                SELECT id,
                       (regexp_match(raw_payload->>'url',
                                     'instagram\.com/(p|reel|tv)/([A-Za-z0-9_-]+)'))[2] AS sc
                FROM place_raw_data
                WHERE provider = 'instagram'
                  AND provider_place_id IS NULL
                  AND raw_payload->>'url' IS NOT NULL
            ),
            singletons AS (
                SELECT sc FROM candidates
                WHERE sc IS NOT NULL
                GROUP BY sc
                HAVING count(*) = 1
            )
            UPDATE place_raw_data p
            SET provider_place_id = c.sc
            FROM candidates c
            JOIN singletons s ON s.sc = c.sc
            WHERE p.id = c.id;
            """
        )
    )

    # 3) instagram_post_cache → place_raw_data 데이터 복사.
    #    메타키 _source/_url을 raw_payload에 주입해 get_cached가 분리할 수 있게 한다.
    #    ON CONFLICT 시 raw_payload/collected_at만 갱신 — place_id는 보존(정제 연결 유지).
    op.execute(
        sa.text(
            """
            INSERT INTO place_raw_data (place_id, provider, provider_place_id, raw_payload, collected_at)
            SELECT NULL,
                   'instagram',
                   c.shortcode,
                   c.payload || jsonb_build_object('_source', c.source, '_url', c.url),
                   c.fetched_at
            FROM instagram_post_cache c
            ON CONFLICT (provider, provider_place_id) WHERE provider_place_id IS NOT NULL
            DO UPDATE SET
                raw_payload = EXCLUDED.raw_payload,
                collected_at = EXCLUDED.collected_at;
            """
        )
    )

    # 4) 정정 가드를 통과하지 못한 NULL provider_place_id 축약본 정리.
    #    같은 shortcode의 신규 full payload 행이 step 3에서 INSERT됐으므로 안전하게 삭제 가능.
    op.execute(
        sa.text(
            """
            DELETE FROM place_raw_data
            WHERE provider = 'instagram'
              AND provider_place_id IS NULL
              AND raw_payload ? 'url';
            """
        )
    )

    # 5) 기존 spots에 caption 백필 — raw_payload에 저장된 caption이 있으면 정제 컬럼으로 옮긴다.
    #    같은 place에 여러 raw 행이 있을 때 임의 1행을 사용 (DISTINCT ON으로 결정적 선택).
    op.execute(
        sa.text(
            """
            UPDATE spots s
            SET caption = sub.caption
            FROM (
                SELECT DISTINCT ON (place_id)
                       place_id, raw_payload->>'caption' AS caption
                FROM place_raw_data
                WHERE provider = 'instagram'
                  AND raw_payload->>'caption' IS NOT NULL
                  AND place_id IS NOT NULL
                ORDER BY place_id, collected_at DESC
            ) sub
            WHERE s.place_id = sub.place_id
              AND s.caption IS NULL;
            """
        )
    )

    # 6) instagram_post_cache 테이블 DROP — 모든 데이터가 place_raw_data로 옮겨졌다.
    op.drop_table("instagram_post_cache")


def downgrade() -> None:
    # 1) instagram_post_cache 재생성 (b7c4d2e9a812과 동일 스키마)
    op.create_table(
        "instagram_post_cache",
        sa.Column("shortcode", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("shortcode"),
    )

    # 2) place_raw_data → instagram_post_cache 복원.
    #    메타키 _source/_url에서 source/url 분해. 메타키가 없으면 'apify' 기본값 + payload->>'url' 사용.
    #    같은 shortcode가 다중이면 가장 최신 행만 살림(DISTINCT ON).
    op.execute(
        sa.text(
            """
            INSERT INTO instagram_post_cache (shortcode, url, payload, source, fetched_at)
            SELECT DISTINCT ON (provider_place_id)
                   provider_place_id,
                   COALESCE(raw_payload->>'_url', raw_payload->>'url', ''),
                   raw_payload - '_source' - '_url',
                   COALESCE(raw_payload->>'_source', 'apify'),
                   collected_at
            FROM place_raw_data
            WHERE provider = 'instagram'
              AND provider_place_id IS NOT NULL
            ORDER BY provider_place_id, collected_at DESC;
            """
        )
    )

    # 3) spots.caption DROP — 백필된 caption 데이터 손실
    op.drop_column("spots", "caption")

    # 4) NULL place_id 행 DELETE — NOT NULL 제약 복원을 위해 필수
    op.execute(
        sa.text(
            """
            DELETE FROM place_raw_data WHERE place_id IS NULL;
            """
        )
    )

    # 5) place_raw_data.place_id NOT NULL 복원
    op.alter_column(
        "place_raw_data",
        "place_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
