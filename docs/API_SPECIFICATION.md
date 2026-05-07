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
| `instagram_location_id` | string \| null |
| `og_title` | string \| null |
| `og_description` | string \| null |

**비고:** `instagram_location_id`는 게시물 내 `<script>` 태그 JSON에서 위치 태그 ID를 추출한 값입니다. 비로그인 상태에서는 인스타그램이 해당 데이터를 숨기는 경우가 많아 `null`로 올 수 있습니다.

**오류**

| 코드 | 조건 |
|------|------|
| `400` | 잘못된 URL 등 (`ValueError`) |
| `404` | OG 제목·설명·이미지가 모두 비어 있음(비공개/삭제 등 추정) |
| `504` | 타임아웃 등 (`TimeoutError`) |
| `500` | Playwright 미초기화 |

#### POST `/instagram/save`

클라이언트가 `/instagram/crawl` 결과(캡션·썸네일)와 네이버 지도에서 선택한 장소 정보를 함께 전달하면, 서버가 한 번의 호출로 Place upsert + Spot 저장을 처리합니다. 서버는 추가로 인스타그램을 재크롤링하지 않습니다(방식 C).

**요청 본문 (`InstagramSaveRequest`)**

| 필드 | 타입 | 필수 | 제약/설명 |
|------|------|------|-----------|
| `instagram_url` | string (URL) | 예 | 인스타그램 게시물 URL |
| `caption` | string \| null | 아니오 | 게시물 캡션 (`/crawl`에서 받은 값) |
| `thumbnail_url` | string \| null | 아니오 | 대표 이미지 URL (`/crawl`에서 받은 값) |
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

### KakaoLoginResponse

| 필드 | 타입 |
|------|------|
| `access_token` | string |
| `token_type` | string (`"bearer"`) |
| `is_new_user` | boolean |

### InstagramSaveResponse

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot` | SpotResponse | 신규 또는 기존 Spot |
| `already_saved` | boolean | 동일 저장소·장소에 이미 Spot이 있었는지 |
| `place_created` | boolean | 이번 호출에서 새 Place가 생성됐는지 |

---

## 미노출 사항

- DB에 `place_reviews` 등이 있어도, 현재 **리뷰 조회/작성 HTTP API는 구현되어 있지 않습니다** (`PlaceReviewResponse`는 스키마만 존재).
- API 경로에 버전 접두사(` /v1` 등)는 없습니다.
- 카카오 로그인은 **모바일 SDK access_token 방식**만 지원합니다. 백엔드는 OAuth code → token 교환을 수행하지 않으므로 `KAKAO_CLIENT_SECRET` / `KAKAO_REDIRECT_URI` 환경변수는 사용하지 않습니다 (`KAKAO_REST_API_KEY`만 정의되어 있으며 현재 호출 경로에서는 사용되지 않습니다).
- 공간 탐색(반경 검색), 창고 멤버 초대/관리, 공개 창고 피드, 장소 DNA 조회 API는 아직 구현되어 있지 않습니다.

---

## 문서 정합성

- 코드 기준 최종 갱신: **2026-05-04** — `POST /auth/kakao` 추가, `POST /instagram/save` 방식 C(요청·응답 스키마 전면 교체) 반영, `InstagramCrawlResponse.instagram_location_id` 필드 보완.
- 앱 라우터 및 Pydantic 스키마와 대조하여 작성되었습니다.
- 상세 필드·예시는 `/docs` 의 OpenAPI 스키마를 기준으로 삼는 것이 가장 정확합니다.
