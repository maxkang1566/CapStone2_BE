"""[일회성] 4차 dry-run 직후 DB 상태 점검."""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("=== 카운트 요약 ===")
for q, label in [
    ("SELECT count(*) FROM places", "places"),
    ("SELECT count(*) FROM spots WHERE deleted_at IS NULL", "spots (active)"),
    ("SELECT count(*) FROM place_raw_data", "place_raw_data (전체)"),
    ("SELECT count(*) FROM place_raw_data WHERE place_id IS NOT NULL", "place_raw_data (place 연결됨)"),
    ("SELECT count(*) FROM place_raw_data WHERE place_id IS NULL", "place_raw_data (orphan, 캐시만)"),
    ("SELECT count(*) FROM place_reviews", "place_reviews"),
    ("SELECT count(*) FROM place_images", "place_images"),
    ("SELECT count(*) FROM instagram_crawl_jobs", "instagram_crawl_jobs"),
]:
    cur.execute(q)
    print(f"  {label}: {cur.fetchone()[0]}")

print("\n=== places by provider source (id 순) ===")
cur.execute("""
    SELECT id, name, category_group
    FROM places
    ORDER BY id
""")
for row in cur.fetchall():
    print(f"  id={row[0]:3} | {row[1]:30} | {row[2] or '-'}")

print("\n=== place_raw_data provider 분포 ===")
cur.execute("""
    SELECT provider, count(*) FROM place_raw_data GROUP BY provider ORDER BY 2 DESC
""")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

print("\n=== place_reviews 분포 (Place별) ===")
cur.execute("""
    SELECT p.id, p.name, count(pr.id) AS reviews
    FROM places p
    LEFT JOIN place_reviews pr ON pr.place_id = p.id
    GROUP BY p.id, p.name
    HAVING count(pr.id) > 0
    ORDER BY p.id
""")
rows = cur.fetchall()
if rows:
    for row in rows:
        print(f"  id={row[0]:3} | {row[1]:30} | reviews={row[2]}")
else:
    print("  (아직 0건 — 블로그 워커 처리 대기 중일 수 있음)")

conn.close()
