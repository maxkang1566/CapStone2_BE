"""[일회성] place_images의 image_url 형태 + 접근성 점검."""
import os
import re
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
import httpx
import psycopg2

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute("""
    SELECT pi.place_id, p.name, pi.image_url, pi.source, pi.created_at
    FROM place_images pi
    JOIN places p ON p.id = pi.place_id
    ORDER BY pi.place_id
    LIMIT 5
""")
samples = cur.fetchall()

print(f"=== 샘플 {len(samples)}건 image_url 분석 ===\n")

for place_id, name, url, source, created_at in samples:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    print(f"[place_id={place_id}] {name}  source={source}  created={created_at:%Y-%m-%d %H:%M}")
    print(f"  도메인: {parsed.netloc}")
    print(f"  경로: {parsed.path[:80]}{'...' if len(parsed.path) > 80 else ''}")
    # 인스타 CDN의 만료 토큰 ('oe=', 'oh=', 'efg=' 등)
    expiry_keys = [k for k in qs.keys() if k in ("oe", "oh", "efg", "_nc_ohc", "_nc_gid")]
    if expiry_keys:
        oe = qs.get("oe", [None])[0]
        if oe:
            try:
                # oe는 unix timestamp를 16진수로 표현
                expiry_ts = int(oe, 16)
                from datetime import datetime, timezone
                expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                expired = expiry_dt < now
                print(f"  oe(만료) 16진수={oe} → UTC {expiry_dt:%Y-%m-%d %H:%M:%S} ({'만료됨' if expired else '유효'})")
            except Exception:
                print(f"  oe={oe} (해석 실패)")
        print(f"  쿼리 파라미터 키: {sorted(qs.keys())[:8]}{'...' if len(qs) > 8 else ''}")
    else:
        print("  (만료 토큰 없음)")

    # 실제 fetch 시도
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url)
            print(f"  HTTP 응답: {resp.status_code}, content-type={resp.headers.get('content-type', '')[:30]}, "
                  f"size={len(resp.content) if resp.status_code == 200 else 0}")
            if resp.status_code != 200:
                print(f"    body[:200] = {resp.text[:200]!r}")
    except Exception as e:
        print(f"  fetch 실패: {type(e).__name__}: {e}")
    print()

conn.close()
