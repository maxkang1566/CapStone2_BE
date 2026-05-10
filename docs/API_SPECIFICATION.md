# Picklog Backend — API 명세서

본 문서는 현재 코드베이스(`app/main.py` 및 `app/routers/*`)에 구현된 HTTP API를 정리한 것입니다.  
배포 환경 예시 베이스 URL: `https://capstone2be-production.up.railway.app`  
로컬 예시: `http://127.0.0.1:8000` (실행 설정에 따름)

OpenAPI(Swagger) UI는 서버 루트 기준 `/docs` 에서 동일 내용을 대화형으로 확인할 수 있습니다.

---

## 공통 사항

### 인증

| 항목 | 내용 |
|------|------|
| 방식 | JWT Bearer (`Authorization: Bearer <access_token>`) |
| 토큰 발급 | `POST /auth/login` (OAuth2 Password Grant 형식의 폼 데이터) 또는 `POST /auth/kakao` (모바일 카카오 SDK access_token 전달) |
| 보호 엔드포인트 | 아래 각 API 표에 **인증** 열 참고 |

OAuth2PasswordBearer의 `tokenUrl`은 `/auth/login` 입니다. 카카오 로그인으로 발급받은 토큰도 동일하게 `Authorization: Bearer <access_token>` 헤더로 사용합니다.

### 오류 응답 형식

FastAPI 기본: HTTP 상태 코드와 함께 JSON 본문에 `detail` 필드(문자열 또는 검증 오류 시 객체 배열)가 올 수 있습니다.

### 페이징 공통

| 파라미터 | 타입 | 기본값 | 제약 |
|----------|------|--------|------|
| `page` | integer | `1` | ≥ 1 |
| `size` | integer | `20` | ≥ 1, 일반적으로 ≤ 100 |

---

## 엔드포인트 목록

### 루트

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/` | 불필요 | 서버 동작 확인용(상태·메시지 JSON) |

**응답 예시 필드:** `status`, `message`, `tech_stack`

---

### 인증 `/auth`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/auth/register` | 불필요 | 회원가입. 기본 저장소(`내 저장소`) 및 소유자 멤버 자동 생성 |
| POST | `/auth/login` | 불필요 | 로그인. 액세스 토큰 발급 |
| POST | `/auth/kakao` | 불필요 | 카카오 OAuth 로그인. 모바일 SDK access_token으로 자체 JWT 발급 |

#### POST `/auth/register`

**Content-Type:** `application/json`

**요청 본문**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `email` | string (이메일) | 예 | |
| `password` | string | 예 | |
| `nickname` | string \| null | 아니오 | |

**응답:** `201 Created` — `UserResponse`  
**오류:** `400` — 이미 사용 중인 이메일 (`detail`: 한글 메시지)

#### POST `/auth/login`

**Content-Type:** `application/x-www-form-urlencoded` (OAuth2 표준, Swagger **Authorize**와 호환)

**폼 필드**

| 필드 | 설명 |
|------|------|
| `username` | 로그인에 사용하는 **이메일** |
| `password` | 비밀번호 |

**응답:** `200 OK`

```json
{
  "access_token": "<JWT>",
  "token_type": "bearer"
}
```

**오류:** `401` — 이메일 또는 비밀번호 불일치

#### POST `/auth/kakao`

**Content-Type:** `application/json`

모바일 클라이언트가 카카오 SDK로 받은 `access_token`을 그대로 전달하면, 백엔드가 카카오 사용자 정보를 조회해 자체 JWT를 발급합니다. 백엔드는 OAuth code → token 교환을 수행하지 않습니다.

**요청 본문 (`KakaoLoginRequest`)**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `access_token` | string | 예 | 모바일 카카오 SDK가 발급한 access_token |

**응답:** `200 OK` — `KakaoLoginResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `access_token` | string | 백엔드가 발급한 JWT |
| `token_type` | string | 항상 `"bearer"` |
| `is_new_user` | boolean | 이번 호출에서 신규 가입이 발생했는지 여부 |

**동작**

1. **kakao_id 매칭** — 동일 `kakao_id`의 기존 사용자가 있으면 그대로 로그인.
2. **email 매칭(계정 병합)** — 위에서 못 찾고, 카카오에서 받은 이메일과 동일한 기존 사용자가 있으면 그 사용자에 `kakao_id`를 연결한 뒤 로그인.
3. **신규 가입** — 둘 다 없으면 새 사용자 생성 + 기본 저장소(`내 저장소`) 및 소유자 멤버 자동 등록 (`/auth/register`와 동일 패턴). 카카오 닉네임/프로필 이미지가 있으면 함께 저장.
4. **이메일 동의 미수신** — 카카오 동의 화면에서 사용자가 이메일을 거절한 경우 `kakao_{kakao_id}@picklog.local` 형식의 임시 이메일을 자동 부여합니다. 이후 사용자가 프로필 수정으로 변경 가능합니다.

**오류**

| 코드 | 조건 |
|------|------|
| `401` | 카카오 토큰이 유효하지 않음 (`detail`: 한글 메시지) |
| `502` | 카카오 서버 연결 실패 또는 사용자 정보 조회 실패 |

---

### 사용자 `/users`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/users/me` | 필요 | 내 프로필 조회 |
| PUT | `/users/me` | 필요 | 프로필 수정 |
| GET | `/users/me/space-dna` | 필요 | 내 공간 DNA 조회 |
| GET | `/users/search` | 필요 | 닉네임 prefix 검색 (창고 초대용 친구 찾기) |

#### GET `/users/me`

**응답:** `200 OK` — `UserResponse`

#### PUT `/users/me`

**요청 본문** (`UserUpdate` — 모두 선택)

| 필드 | 타입 | 설명 |
|------|------|------|
| `nickname` | string \| null | |
| `profile_image` | string \| null | |

**응답:** `200 OK` — `UserResponse`

#### GET `/users/me/space-dna`

**응답:** `200 OK` — `UserSpaceDNAResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `has_data` | boolean | 분석된 DNA 데이터 보유 여부 |
| `mbti_axes` | object \| null | MBTI 4축 + confidence (키 명세는 아래 비고 참조) |
| `preferred_vibe_tags` | object \| null | 선호 분위기 태그 (현 미사용) |
| `total_visits` | integer | 누적 방문 횟수 (`has_data=false`일 때 `0`) |
| `last_analyzed` | datetime \| null | 마지막 분석 시각 |

**비고**: `has_data=false`일 때는 `mbti_axes`, `preferred_vibe_tags`, `last_analyzed`가 모두 `null`이고 `total_visits=0`. 신규 가입자는 항상 이 상태로 응답되며 404가 아닌 200 응답입니다 — 클라이언트는 `has_data` 분기로 빈 상태 화면을 처리하면 됩니다.

`mbti_axes` 키 명세 (AI팀 동결, 2026-05-09): `busy_calm`(붐빔↔여유) / `calm_flashy`(차분↔화려함) / `modern_vintage`(최신↔빈티지) / `premium_value`(고급↔가성비) — 모두 `[-1.0, 1.0]` 범위. `confidence`는 `[0.0, 1.0]`.

#### GET `/users/search`

창고 멤버 초대용 닉네임 prefix 검색. 본인은 자동 제외되며 닉네임이 설정된 사용자만 응답에 포함됩니다. 카카오 미동의 사용자가 임시 이메일(`kakao_{id}@picklog.local`)을 받기 때문에 이메일 검색을 대체합니다.

**쿼리**

| 파라미터 | 필수 | 제약 | 설명 |
|----------|------|------|------|
| `q` | 예 | 1~50자 | 닉네임 prefix |
| `size` | 아니오 | 1~50, 기본 20 | 결과 개수 |

**응답:** `200 OK` — `UserSearchResponse[]` (닉네임 오름차순)

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `nickname` | string |
| `profile_image` | string \| null |

**비고**: 이메일은 응답에 노출하지 않습니다 (privacy).

---

### 저장소 `/storages`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/storages` | 필요 | 내가 멤버인 저장소 목록(소프트 삭제 제외) |
| POST | `/storages` | 필요 | 저장소 생성(요청자 owner) |
| GET | `/storages/{storage_id}` | 필요 | 상세(멤버면 조회 가능) |
| PUT | `/storages/{storage_id}` | 필요 | 수정 — **owner, editor** |
| DELETE | `/storages/{storage_id}` | 필요 | 소프트 삭제 — **owner만** |

**경로 파라미터:** `storage_id` — integer

#### GET `/storages`

**쿼리:** `page`, `size` (위 공통 페이징)

**응답:** `200 OK` — `StorageResponse[]`

#### POST `/storages`

**요청 본문 (`StorageCreate`)**

| 필드 | 타입 | 필수 | 기본값 |
|------|------|------|--------|
| `title` | string | 예 | |
| `description` | string \| null | 아니오 | |
| `is_public` | boolean | 아니오 | `false` |

**응답:** `201 Created` — `StorageResponse`

#### GET `/storages/{storage_id}`

**오류:** `404` 멤버 아님, `403` 역할 없음(해당 작업에 필요한 역할이 아닌 경우는 storages에서 주로 멤버십/역할 메시지)

#### PUT `/storages/{storage_id}`

**요청 본문 (`StorageUpdate`)** — 부분 수정, 모두 선택

| 필드 | 타입 |
|------|------|
| `title` | string \| null |
| `description` | string \| null |
| `is_public` | boolean \| null |

**오류:** `403` viewer 등 수정 불가 역할

#### DELETE `/storages/{storage_id}`

**응답:** `204 No Content`

---

### 창고 멤버 `/storages/{storage_id}/members`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/storages/{storage_id}/members` | 필요 | 멤버 목록 (멤버 누구나) |
| POST | `/storages/{storage_id}/members` | 필요 | `user_id`로 멤버 추가 — **owner만** |
| PATCH | `/storages/{storage_id}/members/{user_id}` | 필요 | 멤버 role 변경 / 소유권 이전 — **owner만** |
| DELETE | `/storages/{storage_id}/members/{user_id}` | 필요 | 멤버 추방 — **owner만** |
| DELETE | `/storages/{storage_id}/members/me` | 필요 | 본인 leave (모든 멤버, owner는 거부) |

#### GET `/storages/{storage_id}/members`

**응답:** `200 OK` — `StorageMemberDetailResponse[]` (`joined_at` 오름차순)

#### POST `/storages/{storage_id}/members`

**요청 본문 (`StorageMemberAddRequest`)**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `user_id` | integer | 예 | 추가할 사용자 ID (`/users/search`로 획득) |
| `role` | string | 예 | `"editor"` 또는 `"viewer"` (owner 직접 지정 불가 — PATCH로 이전) |

**응답:** `201 Created` — `StorageMemberDetailResponse`

**오류**

| 코드 | 조건 |
|------|------|
| `403` | 호출자가 owner가 아님 |
| `404` | 호출자가 저장소 멤버 아님 / 대상 사용자 없음 |
| `409` | 이미 저장소 멤버 |

#### PATCH `/storages/{storage_id}/members/{user_id}`

**요청 본문 (`StorageMemberRoleUpdate`)**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `role` | string | 예 | `"owner"` / `"editor"` / `"viewer"` |

**응답:** `200 OK` — `StorageMemberDetailResponse`

**비고:** `role="owner"`로 변경하면 기존 owner는 자동으로 `editor`로 강등됩니다. 두 UPDATE는 같은 트랜잭션에서 단일 commit으로 처리되어 외부에서 owner 0명/2명 상태를 관측할 수 없습니다 (atomic transfer). 본인을 owner로 다시 지정하면 멱등 no-op.

**오류**

| 코드 | 조건 |
|------|------|
| `403` | 호출자가 owner가 아님 |
| `404` | 대상 멤버 또는 저장소 없음 |
| `409` | 유일한 owner 본인을 강등하려 함 — 먼저 다른 멤버에게 owner 이전 필요 |

#### DELETE `/storages/{storage_id}/members/{user_id}`

owner가 다른 멤버를 추방. 본인 user_id로 호출 시 거절됩니다 (`/members/me` 안내).

**응답:** `204 No Content`

**오류**

| 코드 | 조건 |
|------|------|
| `400` | `user_id`가 호출자 본인 — `/members/me` 사용해야 함 |
| `403` | 호출자가 owner가 아님 |
| `404` | 대상 멤버 또는 저장소 없음 |

#### DELETE `/storages/{storage_id}/members/me`

본인이 저장소에서 떠나기. owner는 호출 거부됩니다 (먼저 owner를 이전하거나 storage 자체를 삭제).

**응답:** `204 No Content`

**오류**

| 코드 | 조건 |
|------|------|
| `400` | 호출자가 owner — 떠날 수 없음 |
| `404` | 호출자가 저장소 멤버 아님 |

---

### 스팟 `/storages/{storage_id}/spots`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/storages/{storage_id}/spots` | 필요 | 스팟 목록 |
| POST | `/storages/{storage_id}/spots` | 필요 | 스팟 생성 — **owner, editor** |
| GET | `/storages/{storage_id}/spots/{spot_id}` | 필요 | 스팟 상세 |
| PUT | `/storages/{storage_id}/spots/{spot_id}` | 필요 | 수정 — **owner, editor** (`is_visited=true` 시 `visited_at` 자동 설정) |
| DELETE | `/storages/{storage_id}/spots/{spot_id}` | 필요 | 소프트 삭제 — **owner, editor** |

#### GET `/storages/{storage_id}/spots`

**쿼리:** `page`, `size`

**응답:** `200 OK` — `SpotResponse[]`

#### POST `/storages/{storage_id}/spots`

**요청 본문 (`SpotCreate`)**

| 필드 | 타입 | 필수 |
|------|------|------|
| `place_id` | integer | 예 |
| `instagram_url` | string \| null | 아니오 |
| `thumbnail_url` | string \| null | 아니오 |
| `user_memo` | string \| null | 아니오 |
| `user_rating` | number \| null | 아니오 |

**오류:** `409` — 동일 저장소에 동일 `place_id`가 이미 존재

**응답:** `201 Created` — `SpotResponse`

#### PUT `/storages/{storage_id}/spots/{spot_id}`

**요청 본문 (`SpotUpdate`)** — 모두 선택

| 필드 | 타입 |
|------|------|
| `instagram_url` | string \| null |
| `thumbnail_url` | string \| null |
| `user_memo` | string \| null |
| `user_rating` | number \| null |
| `is_visited` | boolean \| null |

**비고:** 본문에서 `is_visited`가 `true`로 오고 기존 `visited_at`이 비어 있으면 서버가 `visited_at`을 현재 시각(UTC)으로 설정합니다.

**오류:** `404` 스팟 없음

#### DELETE `/storages/{storage_id}/spots/{spot_id}`

**응답:** `204 No Content`

---

### 장소 `/places`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/places/from-naver` | 필요 | 네이버 장소 ID 기준 Place upsert (블로그 enrichment 백그라운드 트리거) |
| GET | `/places` | 필요 | 장소명 검색 |
| GET | `/places/{place_id}` | 필요 | 장소 상세 |
| GET | `/places/{place_id}/raw-data` | 필요 | 장소별 원천 데이터 목록 |
| GET | `/places/{place_id}/reviews` | 필요 | 장소별 외부 리뷰 목록 (네이버 블로그 enrichment 결과) |
| GET | `/places/{place_id}/space-dna` | 필요 | 장소 공간 DNA 조회 |

#### POST `/places/from-naver`

**요청 본문 (`NaverPlaceUpsertRequest`)**

| 필드 | 타입 | 필수 | 제약/비고 |
|------|------|------|-----------|
| `naver_place_id` | string | 예 | `PlaceRawData.provider_place_id`와 매칭 |
| `name` | string | 예 | |
| `address` | string \| null | 아니오 | |
| `latitude` | number \| null | 아니오 | -90 ~ 90, `longitude`와 함께 있으면 PostGIS POINT 저장 |
| `longitude` | number \| null | 아니오 | -180 ~ 180 |
| `category_group` | string \| null | 아니오 | |
| `phone` | string \| null | 아니오 | |
| `homepage_url` | string \| null | 아니오 | |
| `raw_payload` | object \| null | 아니오 | JSON 객체 |

**응답:** `200 OK` — `NaverPlaceUpsertResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `place_id` | integer | |
| `created` | boolean | 신규 생성 여부 |
| `place` | PlaceResponse | |

**비고:** 동시성 등으로 인한 `IntegrityError` 시 롤백 후 기존 행을 재조회해 `created: false`로 응답할 수 있습니다. 신규 생성 시 네이버 블로그 본문 수집 작업이 BackgroundTasks로 트리거됩니다 (응답에는 영향 없음).

#### GET `/places`

**쿼리**

| 파라미터 | 필수 | 설명 |
|----------|------|------|
| `q` | 예 | 검색어, 최소 길이 1 |
| `page` | 아니오 | 기본 1 |
| `size` | 아니오 | 기본 20, 최대 100 |

**응답:** `200 OK` — `PlaceResponse[]` (이름 `ILIKE %q%`)

#### GET `/places/{place_id}`

**오류:** `404` 장소 없음

#### GET `/places/{place_id}/raw-data`

**응답:** `200 OK` — `PlaceRawDataResponse[]` (`collected_at` 내림차순)

#### GET `/places/{place_id}/reviews`

**쿼리:** `page`, `size`

**응답:** `200 OK` — `PlaceReviewResponse[]` (`collected_at` 내림차순)

**오류:** `404` 장소 없음

#### GET `/places/{place_id}/space-dna`

**응답:** `200 OK` — `PlaceSpaceDNAResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `has_data` | boolean | AI팀 분석된 DNA 보유 여부 |
| `mbti_axes` | object \| null | MBTI 4축 + confidence |
| `ai_summary` | string \| null | AI 요약 (한국어 ~200자) |
| `updated_at` | datetime \| null | 마지막 업데이트 |

**비고:** AI팀이 아직 분석하지 않은 장소는 `has_data=false`로 응답되며 다른 필드는 모두 `null`. `mbti_axes`가 빈 dict `{}`인 경우(분석 시작 전 행만 생성)도 `null`로 정규화됩니다. 키 명세는 위 `GET /users/me/space-dna` 비고 참조.

**오류:** `404` 장소 없음

---

### 인스타그램 `/instagram`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/instagram/crawl` | 불필요 | 게시물 URL 동기 크롤링 (OG 메타) |
| POST | `/instagram/crawl-async` | 불필요 | 비동기 크롤링 큐 등록 (Apify + 캐시) |
| GET | `/instagram/jobs/{job_id}` | 불필요 | 크롤링 잡 상태/결과 폴링 |
| POST | `/instagram/save` | 필요 | 크롤링 결과 + 네이버 장소 정보로 Place·Spot 저장 (수동 폴백) |
| POST | `/instagram/share` | 필요 | 자동 매핑 + 저장 (캐시 hit 동기 / miss 시 잡 enqueue) |
| GET | `/instagram/share-jobs/{job_id}` | 필요 | share 잡 상태/결과 폴링 |

#### POST `/instagram/crawl`

**요청 본문 (`InstagramCrawlRequest`)**

| 필드 | 타입 | 필수 |
|------|------|------|
| `url` | string (URL) | 예 — 인스타그램 게시물 URL |

**응답:** `200 OK` — `InstagramCrawlResponse` (스키마 요약 참조)

**비고:** Playwright 기반 동기 OG 메타 크롤링. Apify 파이프라인을 우선 사용하려면 `/crawl-async`를 사용하세요.

**오류**

| 코드 | 조건 |
|------|------|
| `400` | 잘못된 URL 등 (`ValueError`) |
| `404` | OG 제목·설명·이미지가 모두 비어 있음(비공개/삭제 등 추정) |
| `504` | 타임아웃 등 (`TimeoutError`) |
| `500` | Playwright 미초기화 |

#### POST `/instagram/crawl-async`

**요청 본문 (`InstagramCrawlRequest`)** — 위와 동일

**응답:** `200 OK` — `InstagramCrawlJobEnqueueResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string \| null | UUID. cache hit 시 `null` |
| `status` | string | `"pending"` (잡 등록) 또는 `"done"` (캐시 hit) |
| `result` | InstagramCrawlResponse \| null | `status="done"`일 때만 채워짐 |

**동작**

1. URL에서 shortcode 추출 (실패 시 `400`).
2. `place_raw_data` 캐시 조회 → hit이면 즉시 `status="done"` + `result` 반환 (Apify 호출 없음).
3. miss이면 `instagram_crawl_jobs` 행 생성(`kind="crawl"`) 후 RQ 큐에 enqueue. `job_id` 반환.

**오류**

| 코드 | 조건 |
|------|------|
| `400` | URL이 인스타그램 게시물 형식이 아님 |
| `503` | RQ 큐 미초기화 (Redis 연결 실패) |

#### GET `/instagram/jobs/{job_id}`

`kind="crawl"` 잡 상태 조회 — share 잡은 별도(`/share-jobs/{id}`)로 분리됩니다.

**응답:** `200 OK` — `InstagramJobStatusResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string | |
| `status` | string | `"pending"` / `"done"` / `"failed"` |
| `source` | string \| null | `"apify"` / `"og_fallback"` / `null` (처리 전) |
| `result` | InstagramCrawlResponse \| null | `status="done"`일 때만 채워짐 |
| `error` | string \| null | `status="failed"`일 때 사유 |

**오류:** `404` 잡 없음

#### POST `/instagram/save`

클라이언트가 `/crawl` 또는 `/crawl-async` 결과(캡션·썸네일)와 네이버 지도에서 선택한 장소 정보를 함께 전달하면, 서버가 한 번의 호출로 Place upsert + Spot 저장을 처리합니다. 서버는 추가로 인스타그램을 재크롤링하지 않습니다(방식 C — 수동 매핑 폴백).

**요청 본문 (`InstagramSaveRequest`)**

| 필드 | 타입 | 필수 | 제약/설명 |
|------|------|------|-----------|
| `instagram_url` | string (URL) | 예 | 인스타그램 게시물 URL |
| `caption` | string \| null | 아니오 | 게시물 캡션 |
| `thumbnail_url` | string \| null | 아니오 | 대표 이미지 URL |
| `naver_place_id` | string | 예 | 네이버 장소 ID (`PlaceRawData.provider_place_id`와 매칭) |
| `place_name` | string | 예 | 장소명 |
| `place_address` | string \| null | 아니오 | |
| `latitude` | number \| null | 아니오 | -90 ~ 90, `longitude`와 함께 있으면 PostGIS POINT 저장 |
| `longitude` | number \| null | 아니오 | -180 ~ 180 |
| `category_group` | string \| null | 아니오 | |
| `place_raw_payload` | object \| null | 아니오 | 네이버 SDK 원본 JSON |
| `storage_id` | integer \| null | 아니오 | 미제공 시 요청자의 기본 저장소(가장 먼저 owner가 된 저장소)로 자동 저장 |
| `user_memo` | string \| null | 아니오 | |
| `user_rating` | number \| null | 아니오 | |

**권한:** 대상 저장소의 **owner 또는 editor**

**동작**

1. `storage_id` 미제공 시 요청자의 기본 저장소를 자동 선택합니다.
2. 동일 `storage_id` + `instagram_url` 조합이 이미 있으면 `409`로 거절합니다.
3. `naver_place_id` 기준으로 Place를 찾거나 새로 생성합니다 (`PlaceRawData(provider="naver", provider_place_id=naver_place_id)`로 매칭). 동시성 충돌(`IntegrityError`) 시 롤백 후 재조회.
4. 같은 저장소에 동일 Place의 Spot이 이미 있으면 새로 만들지 않고 기존 Spot을 반환하면서 `already_saved=true`로 표시합니다.
5. 네이버 블로그 본문 수집 잡(`naver_blog_fetch`)을 best-effort로 RQ에 enqueue합니다 (큐 미초기화 시 응답에 영향 없음).

**응답:** `201 Created` — `InstagramSaveResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot` | SpotResponse | 신규 또는 기존 Spot |
| `already_saved` | boolean | 동일 저장소·장소에 이미 Spot이 있었는지 여부 |
| `place_created` | boolean | 이번 호출에서 새로 Place가 생성됐는지 여부 |

**오류**

| 코드 | 조건 |
|------|------|
| `404` | 대상 저장소 없음/멤버 아님, 또는 `storage_id` 미제공 시 기본 저장소 미존재 |
| `403` | viewer 등 저장 권한 없음 |
| `409` | 동일 저장소에 동일 `instagram_url` 이미 존재 |
| `400` | 기타 Spot 생성 오류 |

#### POST `/instagram/share`

자동 매핑 + 저장 진입점 (방식 D, 하이브리드 sync/async). 캡션에서 장소 후보를 추출 → 네이버 Local Search → 유니크 1건이면 자동 저장, 아니면 사용자 선택용 후보 반환.

**요청 본문 (`InstagramShareRequest`)**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `url` | string (URL) | 예 | 인스타그램 게시물 URL |
| `storage_id` | integer \| null | 아니오 | 미제공 시 기본 저장소 자동 선택 |

**응답:** `200 OK` — `InstagramShareEnqueueResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string \| null | UUID. cache hit 시 `null` |
| `status` | string | `"pending"` (잡 등록) 또는 `"done"` (캐시 hit 즉시 처리) |
| `result` | InstagramShareResponse \| null | `status="done"`일 때만 채워짐 |

`result.status` 분기 (`InstagramShareResponse.status`):
- `"saved"` — 자동 저장 성공. `spot`, `already_saved`, `place_created` 사용. 네이버 블로그 enrichment 잡이 best-effort로 enqueue됨.
- `"needs_selection"` — 후보가 2개 이상이라 사용자가 직접 선택해야 함. `crawl_data` + `candidates` 사용. 클라이언트는 사용자 선택 후 `/instagram/save`를 호출.
- `"not_a_place_post"` — 유니크 후보 0개. `crawl_data`만 채워짐 (캡션/썸네일 미리보기), `candidates`는 `null`.

**동작**

1. URL에서 shortcode 추출 (실패 시 `400`).
2. `storage_id` 미제공 시 기본 저장소를 자동 선택.
3. 캐시 hit이면 동기 처리 (`share_post`) → `status="done"` + `result` 반환.
4. miss이면 `instagram_crawl_jobs` 행 생성(`kind="share"`, `user_id`/`storage_id` 포함) 후 RQ 큐에 enqueue → `status="pending"` + `job_id` 반환. 클라이언트는 `/share-jobs/{job_id}`로 폴링.

**오류**

| 코드 | 조건 |
|------|------|
| `400` | URL 형식 오류, 또는 spot 생성 / 파이프라인 도메인 오류 |
| `403` | 저장 권한 없음 |
| `404` | 저장소 없음/멤버 아님 |
| `409` | 동일 `instagram_url` 이미 존재 |
| `502` | 네이버 Local Search 외부 호출 실패 (cache hit 동기 흐름에서만 발생) |
| `503` | RQ 큐 미초기화 |

#### GET `/instagram/share-jobs/{job_id}`

`kind="share"` 잡 폴링. 본인 잡만 조회 가능 (다른 사용자의 잡은 존재 자체를 가리려고 `404`로 응답).

**응답:** `200 OK` — `InstagramShareJobStatusResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `job_id` | string | |
| `status` | string | `"pending"` / `"done"` / `"failed"` |
| `result` | InstagramShareResponse \| null | `status="done"`일 때만 |
| `error` | string \| null | `status="failed"`일 때 사유 |

**오류:** `404` 잡 없음 (또는 다른 사용자의 잡)

---

### 헬스 `/health`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/health/db` | 불필요 | DB `SELECT 1` 연결 확인 |

**응답:** `200 OK` — `{ "status": "ok", "db": "connected" }`  
**오류:** `503` — DB 연결 실패

---

## 스키마 요약 (응답 모델)

### UserResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `email` | string |
| `nickname` | string \| null |
| `profile_image` | string \| null |
| `created_at` | datetime (ISO 8601) |

### UserSearchResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `nickname` | string |
| `profile_image` | string \| null |

### KakaoLoginResponse

| 필드 | 타입 |
|------|------|
| `access_token` | string |
| `token_type` | string (`"bearer"`) |
| `is_new_user` | boolean |

### StorageResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `title` | string |
| `description` | string \| null |
| `is_public` | boolean |
| `created_at` | datetime |
| `deleted_at` | datetime \| null |

### StorageMemberDetailResponse

| 필드 | 타입 |
|------|------|
| `storage_id` | integer |
| `user_id` | integer |
| `role` | string (`"owner"` / `"editor"` / `"viewer"`) |
| `joined_at` | datetime |
| `nickname` | string \| null |
| `profile_image` | string \| null |

### SpotResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `storage_id` | integer |
| `place_id` | integer |
| `added_by` | integer |
| `instagram_url` | string \| null |
| `thumbnail_url` | string \| null |
| `caption` | string \| null |
| `user_memo` | string \| null |
| `user_rating` | number \| null |
| `is_visited` | boolean |
| `visited_at` | datetime \| null |
| `created_at` | datetime |
| `deleted_at` | datetime \| null |

### PlaceResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `name` | string |
| `address` | string \| null |
| `latitude` | number \| null |
| `longitude` | number \| null |
| `category_group` | string \| null |
| `phone` | string \| null |
| `homepage_url` | string \| null |
| `created_at` | datetime |

내부적으로 PostGIS `POINT`는 직렬화 시 위도·경도로 분리되어 노출됩니다.

### PlaceRawDataResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `place_id` | integer |
| `provider` | string \| null |
| `provider_place_id` | string \| null |
| `raw_payload` | object \| null |
| `collected_at` | datetime |

### PlaceReviewResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `place_id` | integer |
| `raw_data_id` | integer \| null |
| `provider` | string \| null |
| `external_review_id` | string \| null |
| `rating` | number \| null |
| `text` | string \| null |
| `reviewed_at` | datetime \| null |
| `collected_at` | datetime |

### PlaceSpaceDNAResponse

| 필드 | 타입 |
|------|------|
| `has_data` | boolean |
| `mbti_axes` | object \| null |
| `ai_summary` | string \| null |
| `updated_at` | datetime \| null |

### UserSpaceDNAResponse

| 필드 | 타입 |
|------|------|
| `has_data` | boolean |
| `mbti_axes` | object \| null |
| `preferred_vibe_tags` | object \| null |
| `total_visits` | integer |
| `last_analyzed` | datetime \| null |

### InstagramCrawlResponse

| 필드 | 타입 | 설명 |
|------|------|------|
| `url` | string (URL) | 정규화된 게시물 URL |
| `caption` | string \| null | 캡션 (Apify는 전문, OG fallback은 일부) |
| `images` | string[] | 대표 이미지 URL 목록 |
| `location_name` | string \| null | 장소명 |
| `instagram_location_id` | string \| null | 위치 태그 고유 ID (비로그인 추출 한계로 `null` 가능) |
| `latitude` | number \| null | 위치 태그 좌표 (Apify에서만) |
| `longitude` | number \| null | 위치 태그 좌표 (Apify에서만) |
| `hashtags` | string[] | 캡션 해시태그 (Apify에서만) |
| `mentions` | string[] | 캡션 멘션 (Apify에서만) |
| `posted_at` | string \| null | 게시 시각 ISO 문자열 (Apify) |
| `owner_username` | string \| null | 작성자 (Apify) |
| `og_title` | string \| null | OG 메타 원본 (OG fallback일 때만) |
| `og_description` | string \| null | OG 메타 원본 |

### InstagramSaveResponse

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot` | SpotResponse | 신규 또는 기존 Spot |
| `already_saved` | boolean | 동일 저장소·장소에 이미 Spot이 있었는지 |
| `place_created` | boolean | 이번 호출에서 새 Place가 생성됐는지 |

### InstagramCrawlJobEnqueueResponse / InstagramJobStatusResponse

`/crawl-async` 응답 및 `/jobs/{id}` 상세는 위 엔드포인트 섹션 참조.

### InstagramShareResponse

| 필드 | 타입 | 사용 분기 |
|------|------|-----------|
| `status` | string | `"saved"` / `"needs_selection"` / `"not_a_place_post"` |
| `spot` | SpotResponse \| null | `saved` |
| `already_saved` | boolean \| null | `saved` |
| `place_created` | boolean \| null | `saved` |
| `crawl_data` | InstagramCrawlData \| null | `needs_selection` / `not_a_place_post` |
| `candidates` | PlaceCandidate[] \| null | `needs_selection` |
| `crawl_source` | string \| null | 디버깅 (`"apify"` / `"og_fallback"`) |

#### InstagramCrawlData

| 필드 | 타입 |
|------|------|
| `url` | string (URL) |
| `caption` | string \| null |
| `thumbnail_url` | string \| null |

#### PlaceCandidate

| 필드 | 타입 | 설명 |
|------|------|------|
| `naver_place_id` | string | |
| `name` | string | |
| `address` | string \| null | |
| `road_address` | string \| null | |
| `latitude` | number \| null | |
| `longitude` | number \| null | |
| `category` | string \| null | |
| `category_group` | string \| null | |
| `phone` | string \| null | |
| `link` | string \| null | |
| `raw_payload` | object \| null | 네이버 Local Search 원본 item — 클라이언트는 그대로 `/instagram/save`의 `place_raw_payload`에 전달 가능 |

### InstagramShareEnqueueResponse / InstagramShareJobStatusResponse

`/share` 응답 및 `/share-jobs/{id}` 상세는 위 엔드포인트 섹션 참조.

---

## 미노출 사항

- 사용자 직접 작성 리뷰 API는 없습니다 (`PlaceReviewResponse`는 외부 출처 — 현재 네이버 블로그 — 수집 전용).
- API 경로에 버전 접두사(` /v1` 등)는 없습니다.
- 카카오 로그인은 **모바일 SDK access_token 방식**만 지원합니다. 백엔드는 OAuth code → token 교환을 수행하지 않으므로 `KAKAO_CLIENT_SECRET` / `KAKAO_REDIRECT_URI` 환경변수는 사용하지 않습니다 (`KAKAO_REST_API_KEY`만 정의되어 있으며 현재 호출 경로에서는 사용되지 않습니다).
- 공간 탐색(반경 검색)·공개 창고 피드는 미구현입니다.
- 유저 DNA 자동 업데이트(방문 체크인 시 누적 평균) 트리거는 미구현 — 현재 `/users/me/space-dna`는 외부에서 데이터가 채워지지 않으면 항상 `has_data=false`로 응답합니다.
- 장소 DNA 분석은 AI팀이 Supabase에 직접 write합니다 — 백엔드에 write API가 없습니다.

---

## 문서 정합성

- 코드 기준 최종 갱신: **2026-05-11**
  - DNA 조회 2종 신설: `GET /places/{id}/space-dna`, `GET /users/me/space-dna` (5/10 작업)
  - 창고 멤버 관리 5종 신설: `GET/POST/PATCH/DELETE /storages/{id}/members[/...]` (5/10)
  - 닉네임 검색 신설: `GET /users/search` (5/10)
  - 인스타 비동기 파이프라인: `POST /instagram/crawl-async`, `GET /instagram/jobs/{id}` (5/6)
  - 인스타 자동 매핑 share: `POST /instagram/share` 비동기화, `GET /instagram/share-jobs/{id}` (5/7)
  - 장소 리뷰 조회: `GET /places/{id}/reviews` (5/5~5/8 enrichment 도입)
  - `SpotResponse`에 `caption` 필드 추가 (5/8 인스타 raw 통합)
  - `InstagramCrawlResponse`에 좌표·해시태그·멘션·작성자·게시 시각 필드 추가 (Apify 응답 반영)
- 앱 라우터 및 Pydantic 스키마와 대조하여 작성되었습니다.
- 상세 필드·예시는 `/docs` 의 OpenAPI 스키마를 기준으로 삼는 것이 가장 정확합니다.
