"""[일회성] 옛 잔존 데이터 정리 — id=14 제외 모든 places 삭제 + crawl_jobs 비움.

CASCADE로 place_raw_data / place_reviews / place_images / place_space_dna /
place_tags / spots가 자동 삭제된다.
"""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = False  # 명시적 트랜잭션
cur = conn.cursor()

try:
    cur.execute("DELETE FROM places WHERE id != 14")
    deleted_places = cur.rowcount
    cur.execute("DELETE FROM place_raw_data WHERE place_id IS NULL")
    deleted_orphan_raw = cur.rowcount
    cur.execute("DELETE FROM instagram_crawl_jobs")
    deleted_jobs = cur.rowcount

    print(f"DELETE places (id != 14): {deleted_places} rows")
    print(f"DELETE place_raw_data (orphans, place_id IS NULL): {deleted_orphan_raw} rows")
    print(f"DELETE instagram_crawl_jobs: {deleted_jobs} rows")

    print("\n=== AFTER (트랜잭션 내부 검증) ===")
    expected = {
        "places": 1,
        "place_raw_data": 3,
        "place_reviews": 10,
        "place_images": 1,
        "instagram_crawl_jobs": 0,
    }
    actual = {}
    for label in ["places", "place_raw_data", "place_reviews", "place_images", "spots", "instagram_crawl_jobs"]:
        cur.execute(f"SELECT count(*) FROM {label}")
        n = cur.fetchone()[0]
        actual[label] = n
        marker = ""
        if label in expected:
            marker = " ✅" if n == expected[label] else f" ⚠️ expected {expected[label]}"
        print(f"  {label}: {n}{marker}")

    cur.execute("SELECT id, name FROM places")
    print(f"\n남은 places: {cur.fetchall()}")

    # 핵심 카운트가 예상과 맞으면 commit
    ok = all(actual[k] == v for k, v in expected.items())
    if ok:
        conn.commit()
        print("\nCOMMIT 완료.")
    else:
        conn.rollback()
        print("\n⚠️ 예상치 불일치 — ROLLBACK. 변경사항 없음.")
except Exception as e:
    conn.rollback()
    print(f"오류 발생, ROLLBACK: {e}")
    raise
finally:
    conn.close()
