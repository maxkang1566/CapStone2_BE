"""[일회성] Apify 월 사용량 체크."""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT count(*) FROM instagram_crawl_jobs
    WHERE source='apify' AND created_at >= date_trunc('month', now())
""")
this_month = cur.fetchone()[0]
print(f"이번 달 Apify 호출 (DB 기준): {this_month}")

cur.execute("SELECT count(*) FROM instagram_crawl_jobs")
print(f"전체 instagram_crawl_jobs (정리 후): {cur.fetchone()[0]}")

# place_raw_data(provider='instagram') = 캐시. 캐시된 shortcode는 Apify 재호출 안 함.
cur.execute("SELECT count(*) FROM place_raw_data WHERE provider='instagram'")
print(f"인스타 캐시 행 (재호출 면제): {cur.fetchone()[0]}")

budget = float(os.getenv("APIFY_MONTHLY_BUDGET_USD", "0"))
cost_per_call = 0.002
print(f"\n--- 예측 ---")
print(f"단가 가정 (코드값): ${cost_per_call:.4f}/call")
print(f"월 예산: ${budget:.2f}")
print(f"한도 트리거: {budget / cost_per_call:.0f} calls 시 OG fallback 시작")
print(f"이번 달 누적 추정 비용: ${this_month * cost_per_call:.4f}")
print(f"100건 추가 시 예상 누적 비용: ${(this_month + 100) * cost_per_call:.4f}")
conn.close()
