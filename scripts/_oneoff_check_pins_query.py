"""[0단계 dry-run] PostGIS 함수(`ST_X/ST_Y/ST_MakeEnvelope`)와 GIST 인덱스 검증.

이 코드베이스에서 SQLAlchemy `func.ST_*` 호출이 처음이라
실제 핸들러 작성 전에 한 번 돌려서 정상 동작·인덱스 사용을 확인한다.

사용:
    poetry run python scripts/_oneoff_check_pins_query.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, text

from app.core.database import SessionLocal
from app.models.models import Place


def check_gist_index(db) -> None:
    """places.coordinate에 GIST 인덱스가 실제 생성돼 있는지 확인."""
    rows = db.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename='places' AND indexdef ILIKE '%gist%'"
        )
    ).fetchall()
    print("--- GIST 인덱스 ---")
    if not rows:
        print("  [경고] places.coordinate에 GIST 인덱스 없음. bbox 쿼리가 seq scan으로 떨어질 수 있음.")
    else:
        for name, ddl in rows:
            print(f"  {name}: {ddl}")
    print()


def check_st_xy(db) -> None:
    """func.ST_X/ST_Y가 SQLAlchemy 콘텍스트에서 정상 호출되는지."""
    print("--- ST_X / ST_Y dry-run ---")
    row = (
        db.query(
            Place.id,
            Place.name,
            func.ST_X(Place.coordinate).label("lng"),
            func.ST_Y(Place.coordinate).label("lat"),
        )
        .filter(Place.coordinate.is_not(None))
        .limit(1)
        .first()
    )
    if row is None:
        print("  좌표 있는 Place 0건 — 결과 검증 못 함(그러나 쿼리 자체는 통과).")
    else:
        print(f"  id={row.id} name={row.name!r} lng={row.lng} lat={row.lat}")
        assert isinstance(row.lng, float) and isinstance(row.lat, float), (
            "ST_X/ST_Y가 float을 반환해야 함"
        )
    print()


def check_make_envelope(db) -> None:
    """func.ST_MakeEnvelope + && 연산자가 SQLAlchemy로 표현되는지."""
    print("--- ST_MakeEnvelope && coordinate dry-run (한국 전체 bbox) ---")
    envelope = func.ST_MakeEnvelope(124.0, 33.0, 132.0, 39.0, 4326)
    n = (
        db.query(Place)
        .filter(Place.coordinate.is_not(None))
        .filter(envelope.op("&&")(Place.coordinate))
        .count()
    )
    print(f"  한국 전체 bbox 안의 좌표 보유 Place 수: {n}")
    print()


def check_explain_uses_gist(db) -> None:
    """동일 쿼리에 EXPLAIN — GIST 인덱스를 실제로 쓰는지."""
    print("--- EXPLAIN ANALYZE ---")
    sql = text(
        "EXPLAIN ANALYZE SELECT id FROM places "
        "WHERE coordinate IS NOT NULL "
        "AND ST_MakeEnvelope(124.0, 33.0, 132.0, 39.0, 4326) && coordinate"
    )
    for row in db.execute(sql).fetchall():
        print(f"  {row[0]}")
    print()


def main() -> None:
    db = SessionLocal()
    try:
        check_gist_index(db)
        check_st_xy(db)
        check_make_envelope(db)
        check_explain_uses_gist(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
