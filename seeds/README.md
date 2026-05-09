# Picklog 운영 Places 시딩

AI팀이 공간 DNA(MBTI 4축) 분석에 쓸 데이터를 운영 Supabase에 채워넣는 작업.
계획 전체는 `~/.claude/plans/ai-delightful-marshmallow.md` (v3) 참조.

## 사전 셋업 (시딩 시작 전 반드시 확인)

1. **Railway에 RQ 워커 떠 있는가?**
   - `Dockerfile`은 uvicorn 단일 CMD라 워커는 자동으로 안 뜸.
   - Railway에 두 번째 서비스로 `python -m app.worker` 추가하거나 Procfile 도입.
   - 확인 방법: 임의 인스타 URL로 dry-run 1건 호출 → 30~60초 안에 status가 `done`/`failed`/분기 status로 바뀌면 OK.
   - 워커 없으면: `/share` 잡 100건 전부 pending에서 멈추고, `naver_blog_fetch`도 안 돌아 `place_reviews`가 영원히 비어 있음.

2. **Railway 환경변수 점검**
   - `APIFY_TOKEN`, `APIFY_INSTAGRAM_ACTOR_ID` — `/share` 동작 필수
   - `APIFY_MONTHLY_BUDGET_USD` — 한도 초과 시 OG fallback (caption 품질↓)
   - `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` — 블로그 리뷰 수집
   - `REDIS_URL` — `/share` cache miss 시 RQ enqueue
   - `DATABASE_URL` — Supabase

3. **운영 alembic head 적용**
   ```
   alembic current
   alembic upgrade head
   ```

4. **시드 계정 등록 (test@example.com)**
   - 미등록이면 한 번만:
     ```bash
     curl -X POST $BASE_URL/auth/register \
       -H "Content-Type: application/json" \
       -d '{"email":"test@example.com","password":"<choose>","nickname":"seed"}'
     ```
   - 회원가입 시 owner Storage가 자동 생성됨 (`auth.py:33-39`).

## 입력 파일

- `instagram_urls.txt` — 메인 시드. `--mode share`에서 한 줄씩 처리.
  - **DNA 4축 양극단 균등 분포** (각 군 12~13건):
    고급/가성비, 화려함/차분, 최신/빈티지, 붐빔/여유
  - 행정구·카테고리도 분산.
  - 캡션에 가게명·주소가 명확한 게시물일수록 자동 매핑 적중률↑.
- `golden_set.csv` — 100건 중 10건의 사전 라벨링 (AI팀 알고리즘 검증용).
- `places_naver_fallback.csv` — 보조 시드. `--mode naver`에서 사용.
  - dry-run 결과 saved 비율이 50% 미만이거나, `seed_run_*_pending.jsonl`에 누적된
    needs_selection 후보를 사람이 골라 채워넣을 때 사용.

## 실행

```bash
# 환경변수
export BASE_URL=https://capstone2be-production.up.railway.app
export SEED_EMAIL=test@example.com
export SEED_PASSWORD=<password>

# 1) dry-run 20~30건 — saved 비율 측정
poetry run python scripts/seed_places.py --mode share --limit 25

# 2) saved 비율 50% 이상이면 전체 실행
poetry run python scripts/seed_places.py --mode share

# (조건부) needs_selection 누적분 보강 — 사람이 fallback CSV 채운 뒤
poetry run python scripts/seed_places.py --mode naver
```

윈도우 PowerShell:
```powershell
$env:BASE_URL = "https://capstone2be-production.up.railway.app"
$env:SEED_EMAIL = "test@example.com"
$env:SEED_PASSWORD = "<password>"

poetry run python scripts/seed_places.py --mode share --limit 25
```

## 출력

- `seed_run_<YYYY-MM-DD>.log` — 행별 실행 로그 (place_id 포함)
- `seed_run_<YYYY-MM-DD>_pending.jsonl` — needs_selection 분기 candidates dump
  - 사람이 보고 후보 1개를 골라 `places_naver_fallback.csv`에 옮기는 식

## 검증 (시딩 후)

시딩 완료 5~15분 뒤 (블로그 리뷰 잡 처리 시간) Supabase SQL Editor에서:

```sql
SELECT count(*) FROM places;
SELECT category_group, count(*) FROM places GROUP BY category_group;
SELECT count(*) FROM places WHERE coordinate IS NULL;
SELECT provider, count(*) FROM place_raw_data GROUP BY provider;
SELECT count(*) FROM place_images WHERE source='instagram';

-- 리뷰 적재 (블로그 잡 후)
SELECT count(DISTINCT place_id) FROM place_reviews;
SELECT place_id, count(*) AS n FROM place_reviews GROUP BY place_id ORDER BY n DESC;

-- 본문/스니펫 길이 분포 (AI팀 임계값 합의용)
SELECT place_id,
       count(*) AS reviews,
       avg(length(text))::int AS avg_len,
       min(length(text))      AS min_len,
       max(length(text))      AS max_len
FROM place_reviews GROUP BY place_id;
```

## AI팀 인터페이스

- 권한: `ai_reader`(places/place_*/place_raw_data SELECT) + `ai_dna_writer`(place_space_dna INSERT/UPDATE)
- DNA 쓰기 키 명세 (plan v3 권장값, AI팀과 합의 후 동결):

```sql
INSERT INTO place_space_dna (place_id, mbti_axes, ai_summary, updated_at)
VALUES (
  :place_id,
  '{"busy_calm":0.0,"calm_flashy":0.0,"modern_vintage":0.0,"premium_value":0.0,"confidence":0.0}'::jsonb,
  :ai_summary,
  NOW()
)
ON CONFLICT (place_id) DO UPDATE SET
  mbti_axes  = EXCLUDED.mbti_axes,
  ai_summary = EXCLUDED.ai_summary,
  updated_at = EXCLUDED.updated_at;
```

- 다중 이미지는 `place_images`(대표 1장)가 아니라
  `place_raw_data WHERE provider='instagram'` → `raw_payload->'images'` 배열에서 파싱.
- 한 장소 전체 입력 조회 SQL은 `~/.claude/plans/ai-delightful-marshmallow.md` "AI팀 조회·쓰기 인터페이스" 섹션 참조.
