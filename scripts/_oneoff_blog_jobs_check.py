"""[일회성] naver_blog_fetch 잡 진행 상태 점검."""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("=== instagram_crawl_jobs 분포 (kind × status) ===")
cur.execute("""
    SELECT kind, status, count(*)
    FROM instagram_crawl_jobs
    GROUP BY kind, status
    ORDER BY kind, status
""")
for row in cur.fetchall():
    print(f"  kind={row[0]:20} | status={row[1]:10} | count={row[2]}")

print("\n=== reviews 안 들어온 Place + 해당 잡 상태 ===")
cur.execute("""
    SELECT p.id, p.name,
           (SELECT count(*) FROM place_reviews pr WHERE pr.place_id = p.id) AS review_count,
           (SELECT json_agg(json_build_object(
               'job_id', j.id, 'status', j.status, 'created_at', j.created_at, 'error', j.error))
            FROM instagram_crawl_jobs j
            WHERE j.kind='naver_blog_fetch'
              AND (j.payload->>'place_id')::int = p.id) AS jobs
    FROM places p
    WHERE NOT EXISTS (SELECT 1 FROM place_reviews pr WHERE pr.place_id = p.id)
    ORDER BY p.id
""")
rows = cur.fetchall()
if not rows:
    print("  (없음 — 모든 Place에 reviews 있음)")
for row in rows:
    print(f"  id={row[0]:3} | {row[1]:30} | reviews={row[2]}")
    if row[3]:
        for job in row[3]:
            err = (job.get('error') or '')[:80]
            print(f"      잡 {job['job_id'][:8]} status={job['status']:10} created={job['created_at'][:19]} err={err}")
    else:
        print("      (잡 없음 — enqueue 안 됨)")

conn.close()
