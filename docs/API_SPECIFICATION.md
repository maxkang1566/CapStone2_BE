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
| 토큰 발급 | `POST /auth/login` (OAuth2 Password Grant 형식의 폼 데이터) |
| 보호 엔드포인트 | 아래 각 API 표에 **인증** 열 참고 |

OAuth2PasswordBearer의 `tokenUrl`은 `/auth/login` 입니다.

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

---

### 사용자 `/users`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/users/me` | 필요 | 내 프로필 조회 |
| PUT | `/users/me` | 필요 | 프로필 수정 |

#### GET `/users/me`

**응답:** `200 OK` — `UserResponse`

#### PUT `/users/me`

**요청 본문** (`UserUpdate` — 모두 선택)

| 필드 | 타입 | 설명 |
|------|------|------|
| `nickname` | string \| null | |
| `profile_image` | string \| null | |

**응답:** `200 OK` — `UserResponse`

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
| POST | `/places/from-naver` | 필요 | 네이버 장소 ID 기준 Place upsert |
| GET | `/places` | 필요 | 장소명 검색 |
| GET | `/places/{place_id}` | 필요 | 장소 상세 |
| GET | `/places/{place_id}/raw-data` | 필요 | 장소별 원천 데이터 목록 |

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

**비고:** 동시성 등으로 인한 `IntegrityError` 시 롤백 후 기존 행을 재조회해 `created: false`로 응답할 수 있습니다.

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

---

### 인스타그램 `/instagram`

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| POST | `/instagram/crawl` | 불필요 | 게시물 URL만 크롤링 |
| POST | `/instagram/save` | 필요 | 크롤링 후 Place/RawData/Image/Spot까지 저장 |

#### POST `/instagram/crawl`

**요청 본문 (`InstagramCrawlRequest`)**

| 필드 | 타입 | 필수 |
|------|------|------|
| `url` | string (URL) | 예 — 인스타그램 게시물 URL |

**응답:** `200 OK` — `InstagramCrawlResponse`

| 필드 | 타입 |
|------|------|
| `url` | string (URL) |
| `caption` | string \| null |
| `images` | string[] |
| `location_name` | string \| null |
| `og_title` | string \| null |
| `og_description` | string \| null |

**오류**

| 코드 | 조건 |
|------|------|
| `400` | 잘못된 URL 등 (`ValueError`) |
| `404` | OG 제목·설명·이미지가 모두 비어 있음(비공개/삭제 등 추정) |
| `504` | 타임아웃 등 (`TimeoutError`) |
| `500` | Playwright 미초기화 |

#### POST `/instagram/save`

**요청 본문 (`InstagramSaveRequest`)**

| 필드 | 타입 | 필수 |
|------|------|------|
| `url` | string (URL) | 예 |
| `storage_id` | integer | 예 |

**권한:** 해당 저장소의 **owner 또는 editor**

**응답:** `201 Created` — `SpotResponse`

**오류**

| 코드 | 조건 |
|------|------|
| `404` | 저장소 없음(멤버 아님) 또는 게시물 메타 비어 있음 |
| `403` | viewer 등 저장 권한 없음 |
| `400` / `504` | 크롤링 단계와 동일 |
| `409` | 동일 저장소에 동일 `instagram_url` 이미 존재 |

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

### StorageResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `title` | string |
| `description` | string \| null |
| `is_public` | boolean |
| `created_at` | datetime |
| `deleted_at` | datetime \| null |

### SpotResponse

| 필드 | 타입 |
|------|------|
| `id` | integer |
| `storage_id` | integer |
| `place_id` | integer |
| `added_by` | integer |
| `instagram_url` | string \| null |
| `thumbnail_url` | string \| null |
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

---

## 미노출 사항

- DB에 `place_reviews` 등이 있어도, 현재 **리뷰 조회/작성 HTTP API는 구현되어 있지 않습니다** (`PlaceReviewResponse`는 스키마만 존재).
- API 경로에 버전 접두사(` /v1` 등)는 없습니다.

---

## 문서 정합성

- 코드 기준 최종 갱신: 앱 라우터 및 Pydantic 스키마와 대조하여 작성되었습니다.
- 상세 필드·예시는 `/docs` 의 OpenAPI 스키마를 기준으로 삼는 것이 가장 정확합니다.
