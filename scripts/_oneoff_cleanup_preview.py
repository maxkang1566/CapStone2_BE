"""[일회성] 정리 전 스냅샷 미리보기. 실행 후 즉시 삭제 예정."""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("=== BEFORE — 현재 카운트 ===")
for q, label in [
    ("SELECT count(*) FROM places", "places"),
    ("SELECT count(*) FROM place_raw_data", "place_raw_data"),
    ("SELECT count(*) FROM place_reviews", "place_reviews"),
    ("SELECT count(*) FROM place_images", "place_images"),
    ("SELECT count(*) FROM spots", "spots"),
    ("SELECT count(*) FROM instagram_crawl_jobs", "instagram_crawl_jobs"),
]:
    cur.execute(q)
    print(f"  {label}: {cur.fetchone()[0]}")

print("\n=== 삭제 대상 (id != 14) ===")
cur.execute("SELECT id, name FROM places WHERE id != 14 ORDER BY id")
for row in cur.fetchall():
    print(f"  {row}")

print("\n=== 보존 (id = 14) ===")
cur.execute("SELECT id, name FROM places WHERE id = 14")
row = cur.fetchone()
print(f"  {row}")

conn.close()
