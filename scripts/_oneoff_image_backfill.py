"""[일회성] place_images의 인스타 CDN URL을 Supabase Storage로 백필.

기존 21건 Place 중 source='instagram'인 행만 처리.
- 원본 URL fetch 200이면 → Storage 업로드 → image_url 갱신 + spots.thumbnail_url도 함께 갱신
- 원본 URL이 이미 만료(403)면 스킵 (id=14 다케오 호르몬 같은 케이스)
- 이미 Supabase 도메인이면 스킵
"""
import os
import sys
from dotenv import load_dotenv
import psycopg2

# 프로젝트 루트 import 가능하도록
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import image_storage  # noqa: E402

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
conn.autocommit = False
cur = conn.cursor()

# instagram raw payload에서 shortcode를 가져오기 위해 join.
cur.execute("""
    SELECT pi.id, pi.place_id, pi.image_url,
           (SELECT prd.provider_place_id FROM place_raw_data prd
            WHERE prd.place_id = pi.place_id AND prd.provider = 'instagram'
            ORDER BY prd.collected_at DESC LIMIT 1) AS shortcode
    FROM place_images pi
    WHERE pi.source = 'instagram'
    ORDER BY pi.place_id
""")
rows = cur.fetchall()
print(f"=== 백필 대상 {len(rows)}건 ===\n")

ok = 0
skip = 0
fail = 0
expired = 0

for img_id, place_id, image_url, shortcode in rows:
    # 이미 Supabase 도메인이면 스킵
    if "supabase.co" in (image_url or ""):
        print(f"  [skip] place_id={place_id} 이미 Supabase URL")
        skip += 1
        continue

    print(f"  [{place_id}] shortcode={shortcode} url={image_url[:80]}...")
    permanent_url = image_storage.upload_instagram_image(
        image_url=image_url,
        shortcode=shortcode,
    )
    if permanent_url is None:
        print(f"    └ 실패(만료 추정 또는 업로드 실패) — 스킵")
        expired += 1
        continue

    # place_images.image_url 갱신
    cur.execute(
        "UPDATE place_images SET image_url=%s WHERE id=%s",
        (permanent_url, img_id),
    )
    # spots.thumbnail_url도 같은 place의 모든 spot에 갱신 (기존 동일 URL이었음을 가정)
    cur.execute(
        "UPDATE spots SET thumbnail_url=%s WHERE place_id=%s",
        (permanent_url, place_id),
    )
    print(f"    └ OK → {permanent_url[:80]}...")
    ok += 1

print()
print(f"=== 결과: ok={ok} expired/fail={expired} skip(이미 영구)={skip} fail={fail} ===")
conn.commit()
print("COMMIT 완료")
conn.close()
