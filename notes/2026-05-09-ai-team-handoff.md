# AI팀 핸드오프 — Picklog 운영 DB 접근 가이드

작성일: 2026-05-09
대상: 공간 DNA(MBTI 4축) 분석 알고리즘 개발팀
운영 DB: Supabase PostgreSQL 15 + PostGIS

---

## TL;DR

- **시딩된 Place 21건** + 각각 리뷰 10건 + 인스타 캡션 + 영구 이미지 1장이 운영 DB에 들어가 있다.
- AI팀은 **Supabase Studio에 초대된 뒤 SQL Editor로 직접 조회**한다.
- 분석 결과는 `place_space_dna(place_id, mbti_axes JSONB, ai_summary TEXT)`에 upsert.
- ⚠️ 캡션 안 다중 이미지 URL은 4~5일 후 만료. **대표 이미지(`place_images`)만 영구 저장**.

---

## 1. DB 접근 — Supabase Studio 초대

### 운영자 측 단계

1. Supabase 프로젝트 → **⚙ Project Settings → Team** (또는 좌측 사이드바 **Team**)
2. **Invite a member** → AI팀 멤버 이메일 입력
3. 권한은 **Developer** (읽기·SQL Editor·테이블 편집 가능, 결제 정보 접근 불가)
4. AI팀 멤버는 초대 메일 수락 후 프로젝트 진입

### AI팀 측 사용

- 좌측 **SQL Editor** 메뉴 → 쿼리 실행 (결과는 그리드 + CSV 내보내기)
- **Table Editor** 메뉴 → 테이블 데이터 직접 탐색
- 쿼리 히스토리는 자동 보존, 즐겨찾기 가능

### 대용량 분석이 필요해질 때

10K행 이상의 결과를 다루거나 학습 파이프라인에 연결하려면 외부 도구나 Python 직접 연결이 필요해진다 — **그 시점에 운영자에게 추가 자격증명 요청**. 현재 단계(시딩 21건 + 100건 확장 예정)에서는 Studio만으로 충분.

---

## 2. 권한 분리 — 추후 도입 예정

현재는 **운영자 + AI팀 모두 Supabase Studio Developer 권한**으로 통합 운영한다. 캡스톤 1차 검증 단계에선 권한 분리보다 빠른 협업이 우선.

본 시딩 100건 적재가 끝나고 알고리즘 v1이 검증되면 다음 정책을 도입 예정:
- `ai_reader` 역할 — 모든 분석 입력 테이블에 SELECT만
- `ai_dna_writer` 역할 — `place_space_dna` 테이블에만 INSERT/UPDATE

그 시점에 별도 자격증명을 발급해 AI팀에 공유한다. 그 전까지는 **Studio 안에서 `place_space_dna` 외 테이블에는 INSERT/UPDATE/DELETE를 자제**하는 식의 매너 합의로 운영.

---

## 3. 데이터 모델 요약 (5개 핵심 테이블)

```
places ─┬─ place_raw_data    (인스타 캡션·해시태그·다중이미지 URL · 네이버 검색 결과)
        ├─ place_images      (대표 thumbnail — Supabase Storage 영구 URL)
        ├─ place_reviews     (네이버 블로그 본문, Place당 최대 10건)
        ├─ place_space_dna   (◄ AI팀이 쓰는 곳)
        ├─ place_tags        (사용자 태그)
        └─ spots             (사용자 storage에 저장된 공유 게시물)
```

| 테이블 | 핵심 필드 | 비고 |
|---|---|---|
| `places` | `id, name, address, category_group, coordinate (PostGIS POINT)` | 좌표 추출은 `ST_X(coordinate::geometry) AS lng, ST_Y(...) AS lat` |
| `place_raw_data` | `place_id, provider, raw_payload JSONB, collected_at` | provider ∈ {`naver`, `instagram`, `naver_blog`} |
| `place_images` | `image_url, source, is_representative` | image_url은 **Supabase Storage 영구 URL** (만료 없음) |
| `place_reviews` | `text, length(text) AS text_len, reviewed_at, collected_at, provider` | 본문 추출 실패 시 스니펫 fallback — 길이로 구분 |
| `place_space_dna` | `place_id (PK), mbti_axes JSONB, ai_summary TEXT, updated_at` | 1:1 upsert, 히스토리 없음 |

---

## 4. 분석 SQL 템플릿

### 한 장소의 모든 분석 입력 (캡션 + 리뷰 + 이미지 + 좌표)

```sql
-- 기본 정보 + 좌표
SELECT id, name, address, category_group,
       ST_X(coordinate::geometry) AS lng,
       ST_Y(coordinate::geometry) AS lat
FROM places WHERE id = :place_id;

-- 인스타 캡션 + 다중 이미지 URL (raw_payload 안)
-- raw_payload->>'caption'        : 캡션 본문(이모지·해시태그 포함)
-- raw_payload->'images'          : 다중 이미지 URL 배열 ⚠️ 4~5일 후 만료
SELECT raw_payload->>'caption' AS caption,
       raw_payload->'images'   AS images_array,
       collected_at
FROM place_raw_data
WHERE place_id = :place_id AND provider = 'instagram'
ORDER BY collected_at DESC;

-- 블로그 리뷰
SELECT text, length(text) AS text_len, reviewed_at, collected_at
FROM place_reviews
WHERE place_id = :place_id
ORDER BY collected_at DESC;

-- 대표 이미지 (영구 URL — 만료 없음)
SELECT image_url, source FROM place_images WHERE place_id = :place_id;
```

### 일괄 분석 — 리뷰 1건 이상 있는 Place

```sql
SELECT p.id, p.name, p.category_group,
       COUNT(pr.id) AS review_count,
       AVG(LENGTH(pr.text))::int AS avg_review_len
FROM places p
LEFT JOIN place_reviews pr ON pr.place_id = p.id
GROUP BY p.id, p.name, p.category_group
HAVING COUNT(pr.id) > 0
ORDER BY review_count DESC;
```

### 본문 vs 스니펫 길이 분포 (임계값 합의용)

```sql
SELECT place_id,
       COUNT(*) AS reviews,
       AVG(LENGTH(text))::int AS avg_len,
       MIN(LENGTH(text)) AS min_len,
       MAX(LENGTH(text)) AS max_len
FROM place_reviews GROUP BY place_id;
```

---

## 5. DNA 결과 쓰기 — `place_space_dna`

### 권장 키 명세 (운영자 ↔ AI팀 합의 필요)

```json
{
  "busy_calm":      0.0,   // -1.0 (붐빔) ~ 1.0 (여유)
  "calm_flashy":    0.0,   // -1.0 (차분) ~ 1.0 (화려함)
  "modern_vintage": 0.0,   // -1.0 (최신) ~ 1.0 (빈티지)
  "premium_value":  0.0,   // -1.0 (고급) ~ 1.0 (가성비)
  "confidence":     0.0    //  0.0 ~ 1.0 (선택)
}
```

`ai_summary`: 한국어 200자 이내 권장.

### Upsert 쿼리

```sql
INSERT INTO place_space_dna (place_id, mbti_axes, ai_summary, updated_at)
VALUES (:place_id, :mbti_axes_jsonb, :ai_summary, NOW())
ON CONFLICT (place_id) DO UPDATE SET
  mbti_axes  = EXCLUDED.mbti_axes,
  ai_summary = EXCLUDED.ai_summary,
  updated_at = EXCLUDED.updated_at;
```

⚠️ `place_space_dna`는 **1:1 upsert로 히스토리 없음**. v1→v2 알고리즘 비교가 필요하면 결과를 별도 CSV로 백업하거나 운영자에게 history 테이블 도입 요청.

---

## 6. 이미지 접근 — 만료 정책

| 출처 | 만료 여부 | 사용 권장 |
|---|---|---|
| `place_images.image_url` | **영구** (Supabase Storage public URL) | ✅ 멀티모달 분석 메인 입력 |
| `place_raw_data(provider='instagram').raw_payload->'images'` 배열 | ⚠️ **4~5일 후 만료** (인스타 CDN) | 다중 이미지 분석은 적재 시점 기준 **3일 이내** 사용 |
| `spots.thumbnail_url` | 영구 (place_images와 동일 URL) | 필요 시 사용 |

→ 다중 이미지가 필요하면 **시딩 직후(2026-05-09 적재)** 분석 시작 권장. 만료 후엔 같은 게시물 재시딩으로 갱신 가능하지만 그동안 그 게시물이 삭제되면 회복 불가.

---

## 7. 현재 시딩 결과 (2026-05-09 기준)

- **places: 21건** (한식 8, 음식점 9, 술집 3, 중식 1)
- **place_reviews: 약 210건** (각 Place당 10건)
- **place_images: 21건** (영구 URL — id=14 하나는 만료된 상태로 남아있음)
- **place_raw_data: 66건** (provider별 — naver 21 / instagram 26 / naver_blog 19)
- **spots: 21건** (test@example.com 시드 계정 storage)

자동 매핑 saved 비율은 LLM disambiguator(Claude Haiku 4.5) 도입으로 80% 도달.
needs_selection 3건과 not_a_place_post 2건은 시딩 대상에서 제외됨.

---

## 8. 체크리스트 (AI팀 시작 전)

- [ ] Supabase 초대 메일 수락 → 프로젝트 진입
- [ ] **SQL Editor**에서 `SELECT id, name FROM places LIMIT 5` 한 번 실행 — 연결 검증
- [ ] `places.coordinate` 좌표 추출 SQL 동작 확인 (§4 참고)
- [ ] `place_space_dna` upsert 쿼리 dry-run (id=14 다케오 호르몬으로 1건 시험 쓰기)
- [ ] 운영자와 DNA 키 명세(영문 키 + 값 범위) 합의 → 본 메모에 동결 요청
- [ ] 알고리즘 v1 결과를 `place_space_dna`에 채우고 운영자에게 검증 요청

---

## 운영자 측 To-Do

- [ ] AI팀 멤버 Supabase Studio Developer 초대 (위 §1)
- [ ] DNA 키 명세 합의 후 본 메모 동결
- [ ] 본 시딩 100건 실행 (현재 25건 → 75건 추가 큐레이션 후)
- [ ] 분포 매트릭스(category_group × 행정구 × review_count_bucket) AI팀에 별도 전달
- [ ] **알고리즘 v1 검증 후** `ai_reader`/`ai_dna_writer` 권한 분리 도입 (§2)
- [ ] 이미지 만료(`raw_payload['images']`) 영구 저장이 필요해지면 별도 마이그레이션
