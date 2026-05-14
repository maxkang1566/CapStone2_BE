# Picklog Backend — API 명세서

본 문서는 현재 코드베이스(`app/main.py`, `app/routers/*`)의 HTTP API를 정리한 것입니다.

- 배포: `https://capstone2be-production.up.railway.app`
- 로컬: `http://127.0.0.1:8000`
- 대화형: 서버 루트 기준 `/docs` (OpenAPI/Swagger)

---

## 공통 사항

### 인증

- 방식: JWT Bearer (`Authorization: Bearer <access_token>`)
- 발급: `POST /auth/login` (OAuth2 Password Grant 폼) 또는 `POST /auth/kakao` (모바일 카카오 SDK access_token)
- OAuth2PasswordBearer의 `tokenUrl`은 `/auth/login`. 카카오로 발급받은 토큰도 동일 헤더로 사용.

### 요청·응답 공통

- 기본 `Content-Type`: `application/json` (예외만 별도 표기)
- 오류 본문: FastAPI 기본 — JSON `detail` 필드(문자열 또는 검증 오류 시 객체 배열)
- 페이징 공통 쿼리: `page`(int, 기본 1, ≥1), `size`(int, 기본 20, ≤100)

---

## 엔드포인트 목록

### 루트

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/` | 불필요 | 서버 동작 확인 (`status`, `message`, `tech_stack`) |

---

### 인증 `/auth`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/auth/register` | 불필요 | 회원가입. 기본 저장소(`내 저장소`) + 소유자 멤버 자동 생성 |
| POST | `/auth/login` | 불필요 | 로그인 (액세스 토큰 발급) |
| POST | `/auth/kakao` | 불필요 | 카카오 OAuth — 모바일 SDK access_token으로 자체 JWT 발급 |

#### POST `/auth/register`

요청: `email`(필수), `password`(필수), `nickname`(선택)
응답: `201` — `UserResponse`
오류: `400` 이메일 중복

#### POST `/auth/login`

`Content-Type: application/x-www-form-urlencoded` (Swagger **Authorize** 호환)
폼 필드: `username`(=이메일), `password`
응답 200:
```json
{ "access_token": "<JWT>", "token_type": "bearer" }
```
오류: `401` 자격증명 불일치

#### POST `/auth/kakao`

요청 (`KakaoLoginRequest`): `access_token`(필수, 카카오 SDK 발급분)
응답 200 (`KakaoLoginResponse`): `access_token`, `token_type="bearer"`, `is_new_user`(bool)

**동작**
1. `kakao_id`로 기존 사용자 매칭 → 로그인.
2. 없으면 카카오 이메일로 매칭 → 기존 사용자에 `kakao_id` 연결 후 로그인 (계정 병합).
3. 둘 다 없으면 신규 가입 + 기본 저장소·소유자 멤버 생성. 카카오 닉네임/프로필 이미지가 있으면 함께 저장.
4. 이메일 동의 거부 시 `kakao_{kakao_id}@picklog.local` 임시 이메일 부여 (이후 프로필 수정으로 변경 가능).

오류: `401` 카카오 토큰 무효 / `502` 카카오 서버 호출 실패

---

### 사용자 `/users`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/users/me` | 필요 | 내 프로필 |
| PUT | `/users/me` | 필요 | 프로필 수정 (`nickname`, `profile_image` 모두 선택) |
| GET | `/users/me/space-dna` | 필요 | 내 공간 DNA |
| POST | `/users/me/space-dna` | 필요 | 온보딩 16문항 결과로 공간 DNA 최초 1회 저장 |
| GET | `/users/search` | 필요 | 닉네임 prefix 검색 (창고 초대용) |

#### GET `/users/me/space-dna` — `UserSpaceDNAResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `has_data` | boolean | 저장된 DNA 보유 여부 |
| `mbti_axes` | object \| null | 3축 비율 (아래 명세) |
| `preferred_vibe_tags` | object \| null | 선호 분위기 (현 미사용) |
| `total_visits` | integer | 누적 방문 횟수 (`has_data=false`일 때 `0`) |
| `last_analyzed` | datetime \| null | 마지막 분석 시각 |

`has_data=false`이면 다른 필드는 모두 `null`/`0`. 신규 가입자도 200 응답 — 클라이언트는 `has_data` 분기로 빈 상태 처리.

**`mbti_axes` 키 명세 (3축, 2026-05-14 동결)**

| 영어 키 | 한글 라벨 | 두 유형 |
|---|---|---|
| `color` | 자극 강도 | `high` ↔ `mild` |
| `density` | 분위기 밀도 | `dense` ↔ `sparse` |
| `form` | 트렌디함 | `fresh` ↔ `vintage` |

GET 응답의 `mbti_axes`는 저장 형태와 상관없이 항상 중첩 dict로 정규화돼 반환됩니다.
```json
{
  "mbti_axes": {
    "color":   {"high": 30.0, "mild": 70.0},
    "density": {"dense": 60.0, "sparse": 40.0},
    "form":    {"fresh": 80.0, "vintage": 20.0}
  }
}
```

내부 저장은 두 형태 중 하나입니다 — (a) 온보딩 POST가 저장한 중첩 dict, (b) AI 자동 트리거가 저장한 단일 값(`{color: 25.8, ...}`, 첫 유형 비율을 의미). GET 응답에서는 (b)를 (a) 형태로 펼쳐서 일관된 구조로 노출합니다.

#### POST `/users/me/space-dna` — `UserSpaceDNAOnboardingRequest`

회원가입 후 온보딩 16문항을 통해 프론트엔드가 계산한 3축 비율을 최초 1회 저장합니다.

요청 본문:
```json
{
  "mbti_axes": {
    "color":   {"high": 30, "mild": 70},
    "density": {"dense": 60, "sparse": 40},
    "form":    {"fresh": 80, "vintage": 20}
  }
}
```

규칙:
- 축 키는 정확히 `{color, density, form}` — 누락·추가 시 422.
- 각 축의 유형 키도 위 명세와 정확히 일치 — 다른 키 시 422.
- 각 값은 `0 ~ 100` 실수, 같은 축의 두 값 합은 `100` (`±0.01` 허용) — 위반 시 422.

응답:
- `201` — `UserSpaceDNAResponse`. 저장한 값을 그대로 반환.
- `409` — 이미 mbti_axes가 채워진 상태 (재호출 차단).
- `422` — 입력 검증 실패.

특이 사항:
- AI 자동 트리거(`rebuild_user_dna`)가 먼저 `mbti_axes={}` 빈 행을 만든 시나리오에서는 그 빈 행을 UPDATE로 채우고 201. 기존 `total_visits`는 보존돼 응답에 반영됨.
- 재호출 정책상 최초 1회만 채울 수 있음. 이후 클라이언트가 사용자에게 보여줄 시점에 GET으로 확인하거나 401 분기 처리.

#### GET `/users/search`

창고 멤버 초대용 닉네임 prefix 검색. 본인 자동 제외, 닉네임 미설정 사용자 제외, 이메일 비노출 (privacy). 카카오 임시 이메일 사용자 때문에 이메일 검색 대신 닉네임 검색 채택.

쿼리: `q`(필수, 1~50자), `size`(선택, 1~50, 기본 20)
응답 200: `UserSearchResponse[]` (닉네임 오름차순) — `id`, `nickname`, `profile_image`

---

### 저장소 `/storages`

| 메서드 | 경로 | 인증 | 권한 | 설명 |
|--------|------|------|------|------|
| GET | `/storages` | 필요 | 멤버 | 내가 멤버인 저장소 목록(소프트 삭제 제외) — `page`/`size` |
| POST | `/storages` | 필요 | — | 저장소 생성 (요청자 owner) |
| GET | `/storages/{storage_id}` | 필요 | 멤버 | 상세 |
| PUT | `/storages/{storage_id}` | 필요 | owner, editor | 수정 (부분 수정, 모두 선택) |
| DELETE | `/storages/{storage_id}` | 필요 | owner | 소프트 삭제 (`204`) |

**POST 요청** (`StorageCreate`): `title`(필수), `description`(선택), `is_public`(선택, 기본 `false`)
**PUT 요청** (`StorageUpdate`): `title`, `description`, `is_public` 전부 선택

오류 공통: `404` 멤버 아님 / `403` 역할 부족(예: PUT을 viewer가 호출)

---

### 창고 멤버 `/storages/{storage_id}/members`

| 메서드 | 경로 | 권한 | 설명 |
|--------|------|------|------|
| GET | `/members` | 멤버 | 멤버 목록 (`joined_at` 오름차순) |
| POST | `/members` | owner | `user_id`로 멤버 추가 |
| PATCH | `/members/{user_id}` | owner | role 변경 / 소유권 이전 |
| DELETE | `/members/{user_id}` | owner | 멤버 추방 (`204`) |
| DELETE | `/members/me` | 멤버 | 본인 leave (owner 거부, `204`) |

#### POST `/members` — `StorageMemberAddRequest`

| 필드 | 타입 | 설명 |
|------|------|------|
| `user_id` | integer | 추가할 사용자 (`/users/search`로 획득) |
| `role` | string | `"editor"` 또는 `"viewer"` (owner 직접 지정 불가) |

응답 `201` — `StorageMemberDetailResponse`
오류: `403` 호출자 owner 아님 / `404` 호출자 멤버 아님 또는 대상 사용자 없음 / `409` 이미 멤버

#### PATCH `/members/{user_id}` — `StorageMemberRoleUpdate`

`role`: `"owner"` / `"editor"` / `"viewer"`

**소유권 이전**: `role="owner"` 지정 시 기존 owner는 자동 `editor` 강등. 두 UPDATE를 같은 트랜잭션에서 단일 commit — 외부에서 owner 0/2명 상태 관측 불가 (atomic transfer). 본인 재지정은 멱등 no-op.

오류: `403` 호출자 owner 아님 / `404` 대상/저장소 없음 / `409` 유일한 owner 본인 강등 시도

#### DELETE `/members/{user_id}`

본인 user_id로 호출 시 거절 (`/members/me` 안내).
오류: `400` 본인 호출 / `403` 호출자 owner 아님 / `404` 대상/저장소 없음

#### DELETE `/members/me`

owner는 호출 거부 — 먼저 owner 이전 또는 storage 삭제 필요.
오류: `400` 호출자가 owner / `404` 호출자 멤버 아님

---

### 스팟 `/storages/{storage_id}/spots`

| 메서드 | 경로 | 권한 | 설명 |
|--------|------|------|------|
| GET | `/spots` | 멤버 | 목록 (`page`/`size`) |
| POST | `/spots` | owner, editor | 생성 |
| GET | `/spots/{spot_id}` | 멤버 | 상세 |
| PUT | `/spots/{spot_id}` | owner, editor | 수정 |
| DELETE | `/spots/{spot_id}` | owner, editor | 소프트 삭제 (`204`) |

#### POST — `SpotCreate`

| 필드 | 타입 | 필수 |
|------|------|------|
| `place_id` | integer | 예 |
| `instagram_url` | string \| null | 아니오 |
| `thumbnail_url` | string \| null | 아니오 |
| `user_memo` | string \| null | 아니오 |
| `user_rating` | number \| null | 아니오 |

오류: `409` 동일 저장소에 동일 `place_id` 존재

#### PUT — `SpotUpdate` (모두 선택)

`instagram_url`, `thumbnail_url`, `user_memo`, `user_rating`, `is_visited`

`is_visited=true`로 오면서 기존 `visited_at`이 비어 있으면 서버가 현재 시각(UTC)으로 자동 설정.

---

### 장소 `/places`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/places/from-naver` | 필요 | 네이버 장소 ID로 Place upsert (블로그 enrichment 백그라운드 트리거) |
| GET | `/places` | 필요 | 장소명 검색 (`q` 필수, `ILIKE %q%`) |
| GET | `/places/{place_id}` | 필요 | 상세 |
| GET | `/places/{place_id}/raw-data` | 필요 | 원천 데이터 목록 (`collected_at` 내림차순) |
| GET | `/places/{place_id}/reviews` | 필요 | 외부 리뷰 (네이버 블로그 enrichment 결과) |
| GET | `/places/{place_id}/space-dna` | 필요 | 장소 공간 DNA |

#### POST `/places/from-naver` — `NaverPlaceUpsertRequest`

| 필드 | 타입 | 필수 | 비고 |
|------|------|------|------|
| `naver_place_id` | string | 예 | `PlaceRawData.provider_place_id`와 매칭 |
| `name` | string | 예 | |
| `address` | string \| null | 아니오 | |
| `latitude` | number \| null | 아니오 | -90~90, longitude와 함께 있으면 PostGIS POINT 저장 |
| `longitude` | number \| null | 아니오 | -180~180 |
| `category_group` | string \| null | 아니오 | |
| `phone` | string \| null | 아니오 | |
| `homepage_url` | string \| null | 아니오 | |
| `raw_payload` | object \| null | 아니오 | |

응답 200 (`NaverPlaceUpsertResponse`): `place_id`, `created`(bool), `place`(`PlaceResponse`)

동시성 충돌(`IntegrityError`) 시 롤백 후 재조회해 `created=false` 반환. 신규 생성 시 네이버 블로그 본문 수집이 BackgroundTasks로 트리거 (응답 영향 없음).

#### GET `/places/{place_id}/space-dna` — `PlaceSpaceDNAResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `has_data` | boolean | AI팀 분석 보유 여부 |
| `mbti_axes` | object \| null | 3축 (키는 위 `/users/me/space-dna` 참조). 단일 값 형태로 저장됨 |
| `ai_summary` | string \| null | AI 요약 (~200자 한국어) |
| `updated_at` | datetime \| null | 최종 업데이트 |

미분석 장소는 `has_data=false` + 나머지 `null`. `mbti_axes`가 빈 dict `{}`면 `null`로 정규화.
오류: `404` 장소 없음

---

### 인스타그램 `/instagram`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/instagram/crawl` | 불필요 | 게시물 URL 동기 크롤링 (Playwright + OG 메타) |
| POST | `/instagram/crawl-async` | 불필요 | 비동기 크롤링 큐 등록 (Apify + 캐시) |
| GET | `/instagram/jobs/{job_id}` | 불필요 | 크롤링 잡 폴링 (`kind="crawl"` 전용) |
| POST | `/instagram/save` | 필요 | 크롤링 결과 + 네이버 장소로 Place·Spot 저장 (수동 폴백) |
| POST | `/instagram/share` | 필요 | 자동 매핑+저장 (캐시 hit 동기 / miss 시 잡 enqueue) |
| GET | `/instagram/share-jobs/{job_id}` | 필요 | share 잡 폴링 (본인 잡만) |

#### POST `/instagram/crawl`

요청 (`InstagramCrawlRequest`): `url`(필수, 인스타 게시물 URL)
응답 200: `InstagramCrawlResponse` (스키마 참조). Apify 파이프라인을 우선하려면 `/crawl-async` 사용.
오류: `400` 잘못된 URL / `404` OG 데이터 전부 비어 있음(비공개·삭제 추정) / `504` 타임아웃 / `500` Playwright 미초기화

#### POST `/instagram/crawl-async`

요청: `InstagramCrawlRequest` (위와 동일)
응답 200 (`InstagramCrawlJobEnqueueResponse`):

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string \| null | UUID. cache hit 시 `null` |
| `status` | string | `"pending"` 또는 `"done"` (캐시 hit) |
| `result` | InstagramCrawlResponse \| null | `done`일 때만 |

**동작**
1. URL에서 shortcode 추출 (실패 → `400`).
2. `place_raw_data` 캐시 hit → 즉시 `done`+result (Apify 호출 없음).
3. miss → `instagram_crawl_jobs`(`kind="crawl"`) 행 생성 + RQ enqueue → `job_id` 반환.

오류: `400` URL 형식 / `503` RQ 큐 미초기화

#### GET `/instagram/jobs/{job_id}` — `InstagramJobStatusResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string | |
| `status` | string | `"pending"` / `"done"` / `"failed"` |
| `source` | string \| null | `"apify"` / `"og_fallback"` / `null` |
| `result` | InstagramCrawlResponse \| null | `done`일 때만 |
| `error` | string \| null | `failed`일 때 사유 |

오류: `404` 잡 없음

#### POST `/instagram/save`

`/crawl(-async)` 결과(캡션·썸네일) + 네이버 지도 선택 장소를 한 번에 보내 Place upsert + Spot 저장 (방식 C — 수동 폴백). 서버는 인스타 재크롤링 안 함.

요청 (`InstagramSaveRequest`):

| 필드 | 타입 | 필수 | 비고 |
|------|------|------|------|
| `instagram_url` | string (URL) | 예 | |
| `caption` | string \| null | 아니오 | |
| `thumbnail_url` | string \| null | 아니오 | |
| `naver_place_id` | string | 예 | `PlaceRawData.provider_place_id` 매칭 |
| `place_name` | string | 예 | |
| `place_address` | string \| null | 아니오 | |
| `latitude` | number \| null | 아니오 | -90~90, longitude와 같이 오면 POINT 저장 |
| `longitude` | number \| null | 아니오 | -180~180 |
| `category_group` | string \| null | 아니오 | |
| `place_raw_payload` | object \| null | 아니오 | 네이버 SDK 원본 |
| `storage_id` | integer \| null | 아니오 | 미제공 시 요청자 기본 저장소 (가장 먼저 owner된 곳) |
| `user_memo` | string \| null | 아니오 | |
| `user_rating` | number \| null | 아니오 | |

권한: 대상 저장소의 **owner 또는 editor**

**동작**
1. `storage_id` 미제공 시 기본 저장소 자동 선택.
2. 동일 `storage_id`+`instagram_url` 존재 시 `409`.
3. `naver_place_id` 기준 Place 조회/생성 (동시성 충돌 시 롤백 후 재조회).
4. 같은 저장소에 동일 Place의 Spot이 있으면 기존 Spot 반환 + `already_saved=true`.
5. 네이버 블로그 본문 수집 잡(`naver_blog_fetch`) best-effort enqueue (큐 미초기화 시 응답 무영향).

응답 `201` (`InstagramSaveResponse`): `spot`(`SpotResponse`), `already_saved`(bool), `place_created`(bool)
오류: `404` 저장소 없음/멤버 아님 또는 기본 저장소 미존재 / `403` 권한 부족 / `409` 동일 `instagram_url` 존재 / `400` 기타 생성 오류

#### POST `/instagram/share`

자동 매핑+저장 (방식 D, 하이브리드 sync/async). 캡션 → 장소 후보 추출 → 네이버 Local Search → 유니크 1건이면 자동 저장, 아니면 후보 반환.

요청 (`InstagramShareRequest`): `url`(필수), `storage_id`(선택, 미제공 시 기본 저장소)

응답 200 (`InstagramShareEnqueueResponse`):

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string \| null | cache hit 시 `null` |
| `status` | string | `"pending"` 또는 `"done"` |
| `result` | InstagramShareResponse \| null | `done`일 때만 |

`result.status` 분기:
- `"saved"` — 자동 저장 성공. `spot`, `already_saved`, `place_created` 사용. 블로그 enrichment 잡 best-effort enqueue.
- `"needs_selection"` — 후보 ≥2. `crawl_data`+`candidates` 반환 → 사용자 선택 후 클라이언트가 `/instagram/save` 호출.
- `"not_a_place_post"` — 후보 0. `crawl_data`만 반환.

**동작**: shortcode 추출 → `storage_id` 자동 선택 → 캐시 hit이면 동기 처리(`status="done"`), miss이면 `instagram_crawl_jobs`(`kind="share"`, `user_id`/`storage_id` 포함) 생성 + enqueue (`status="pending"`). 폴링은 `/share-jobs/{job_id}`.

오류: `400` URL/도메인 오류 / `403` 권한 부족 / `404` 저장소 없음/멤버 아님 / `409` 동일 `instagram_url` 존재 / `502` 네이버 Local Search 실패(cache hit 동기 흐름) / `503` RQ 큐 미초기화

#### GET `/instagram/share-jobs/{job_id}` — `InstagramShareJobStatusResponse`

본인 잡만 조회 가능. 타 사용자 잡은 존재 가림용으로 `404`.

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string | |
| `status` | string | `"pending"` / `"done"` / `"failed"` |
| `result` | InstagramShareResponse \| null | `done`일 때만 |
| `error` | string \| null | `failed`일 때 |

---

### 헬스 `/health`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/health/db` | 불필요 | DB `SELECT 1` |

응답 200: `{ "status": "ok", "db": "connected" }` / 오류: `503` DB 실패

---

## 스키마 요약

엔드포인트 섹션에 이미 노출된 스키마(`UserSpaceDNAResponse`, `PlaceSpaceDNAResponse`, `InstagramSaveResponse`, `InstagramCrawlJobEnqueueResponse`, `InstagramJobStatusResponse`, `InstagramShareEnqueueResponse`, `InstagramShareJobStatusResponse` 등)는 생략. 아래는 공용·재사용 모델 위주.

### UserResponse
`id`, `email`, `nickname?`, `profile_image?`, `created_at`(ISO 8601)

### UserSearchResponse
`id`, `nickname`, `profile_image?`

### KakaoLoginResponse
`access_token`, `token_type`(`"bearer"`), `is_new_user`(bool)

### StorageResponse
`id`, `title`, `description?`, `is_public`, `created_at`, `deleted_at?`

### StorageMemberDetailResponse
`storage_id`, `user_id`, `role`(`owner`/`editor`/`viewer`), `joined_at`, `nickname?`, `profile_image?`

### SpotResponse
`id`, `storage_id`, `place_id`, `added_by`, `instagram_url?`, `thumbnail_url?`, `caption?`, `user_memo?`, `user_rating?`, `is_visited`, `visited_at?`, `created_at`, `deleted_at?`

### PlaceResponse
`id`, `name`, `address?`, `latitude?`, `longitude?`, `category_group?`, `phone?`, `homepage_url?`, `created_at`
※ PostGIS `POINT`는 직렬화 시 위도·경도로 분리 노출.

### PlaceRawDataResponse
`id`, `place_id`, `provider?`, `provider_place_id?`, `raw_payload?`, `collected_at`

### PlaceReviewResponse
`id`, `place_id`, `raw_data_id?`, `provider?`, `external_review_id?`, `rating?`, `text?`, `reviewed_at?`, `collected_at`

### InstagramCrawlResponse

| 필드 | 타입 | 설명 |
|------|------|------|
| `url` | string (URL) | 정규화된 게시물 URL |
| `caption` | string \| null | Apify=전문, OG fallback=일부 |
| `images` | string[] | 대표 이미지 URL |
| `location_name` | string \| null | 장소명 |
| `instagram_location_id` | string \| null | 비로그인 추출 한계로 `null` 가능 |
| `latitude` / `longitude` | number \| null | Apify에서만 |
| `hashtags` / `mentions` | string[] | Apify에서만 |
| `posted_at` | string \| null | 게시 시각 ISO (Apify) |
| `owner_username` | string \| null | 작성자 (Apify) |
| `og_title` / `og_description` | string \| null | OG fallback일 때 원본 |

### InstagramShareResponse

| 필드 | 타입 | 사용 분기 |
|------|------|-----------|
| `status` | string | `"saved"` / `"needs_selection"` / `"not_a_place_post"` |
| `spot` | SpotResponse \| null | `saved` |
| `already_saved` / `place_created` | boolean \| null | `saved` |
| `crawl_data` | InstagramCrawlData \| null | `needs_selection`, `not_a_place_post` |
| `candidates` | PlaceCandidate[] \| null | `needs_selection` |
| `crawl_source` | string \| null | 디버깅 (`"apify"`/`"og_fallback"`) |

**InstagramCrawlData**: `url`, `caption?`, `thumbnail_url?`

**PlaceCandidate**: `naver_place_id`, `name`, `address?`, `road_address?`, `latitude?`, `longitude?`, `category?`, `category_group?`, `phone?`, `link?`, `raw_payload?` (네이버 Local Search 원본 — 클라이언트가 그대로 `/instagram/save`의 `place_raw_payload`로 전달 가능)

---

## 미노출 사항

- 사용자 직접 작성 리뷰 API 없음 (`PlaceReviewResponse`는 외부 출처 — 현재 네이버 블로그 — 수집 전용).
- API 경로에 버전 접두사(`/v1` 등) 없음.
- 카카오는 **모바일 SDK access_token 방식**만 지원 — 백엔드는 OAuth code↔token 교환 미수행. `KAKAO_CLIENT_SECRET`/`KAKAO_REDIRECT_URI` 미사용 (`KAKAO_REST_API_KEY`만 정의, 현 호출 경로 미사용).
- 공간 탐색(반경 검색)·공개 창고 피드 미구현.
- 유저 DNA 자동 업데이트(방문 체크인 누적 평균) 트리거 미구현 — `/users/me/space-dna`는 외부 데이터 미충전 시 항상 `has_data=false`.
- 장소 DNA 분석은 AI팀이 Supabase에 직접 write — 백엔드에 write API 없음.

---

## 문서 정합성

- 코드 기준 최종 갱신: **2026-05-11**
  - DNA 조회 2종 신설: `GET /places/{id}/space-dna`, `GET /users/me/space-dna` (5/10)
  - 창고 멤버 관리 5종 신설: `GET/POST/PATCH/DELETE /storages/{id}/members[/...]` (5/10)
  - 닉네임 검색 신설: `GET /users/search` (5/10)
  - 인스타 비동기 파이프라인: `POST /instagram/crawl-async`, `GET /instagram/jobs/{id}` (5/6)
  - 인스타 자동 매핑 share: `POST /instagram/share` 비동기화, `GET /instagram/share-jobs/{id}` (5/7)
  - 장소 리뷰 조회: `GET /places/{id}/reviews` (5/5~5/8 enrichment 도입)
  - `SpotResponse`에 `caption` 추가 (5/8 인스타 raw 통합)
  - `InstagramCrawlResponse`에 좌표·해시태그·멘션·작성자·게시 시각 추가 (Apify 반영)
- 라우터/Pydantic 스키마 대조 작성. 상세 필드·예시는 `/docs` OpenAPI 스키마가 우선.
