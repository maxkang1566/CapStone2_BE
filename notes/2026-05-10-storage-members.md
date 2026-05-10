# 창고 멤버 초대·관리 API (2026-05-10)

## 작업 내용

- 신규 엔드포인트 6개:
  - `GET /users/search?q=&size=` — 닉네임 prefix 검색 (창고 초대용 친구 찾기)
  - `POST /storages/{id}/members` — owner가 user_id로 멤버 추가
  - `GET /storages/{id}/members` — 멤버 목록 (멤버 누구나)
  - `PATCH /storages/{id}/members/{user_id}` — role 변경 (owner 전용, owner 이전 atomic)
  - `DELETE /storages/{id}/members/{user_id}` — 멤버 추방 (owner 전용, 본인 추방은 거부)
  - `DELETE /storages/{id}/members/me` — 본인 leave (owner는 거부)
- 신규 스키마 4개:
  - `UserSearchResponse` (`app/schemas/user.py`)
  - `StorageMemberAddRequest` / `StorageMemberRoleUpdate` / `StorageMemberDetailResponse` (`app/schemas/storage.py`)
- 신규 헬퍼 2개 (`app/routers/storages.py` 내부):
  - `_get_target_member` — 대상 멤버 조회 + 404 처리
  - `_to_member_detail` — `StorageMember` + `User` relationship → 응답 DTO 조립
- 마이그레이션 없음. 기존 `storage_members` 테이블 그대로 사용.

## 결정 이유 (WHY)

### 왜 닉네임 검색 엔드포인트인가 (이메일 검색이 아니라)
- 카카오 로그인 사용자가 이메일 동의를 하지 않으면 `kakao_{id}@picklog.local` 임시 이메일이
  발급된다 (`app/routers/auth.py:95`). 친구가 이 임시 이메일을 알 방법이 없어 이메일 검색만으로는
  카카오 사용자를 절대 찾을 수 없다.
- 카카오 nickname은 카카오 SDK 응답에서 거의 항상 채워진다 (`auth.py:76`).
- 모바일 앱의 친구 찾기 UX와도 일치: 닉네임 prefix → 추천 리스트 → 선택 → user_id로 추가.

### 왜 owner 1명 단일 모델인가
- 기존 코드(`auth.py:38`, `storages.py:63`)가 가입 시 owner 1명을 자동 생성하는 단일 모델을
  전제. 다중 owner로 확장하면 transfer 권한·우선순위·삭제권 충돌 처리가 추가된다.
- 협업이 더 필요해지면 `editor` role이 사실상 co-owner 역할을 한다 (storage 수정 가능,
  storage 삭제만 owner 전용). 다중 owner는 YAGNI.

### 왜 transfer를 별도 엔드포인트가 아닌 `PATCH role="owner"`로 묶었는가
- 별도 `POST /transfer-ownership`을 만들면 권한·검증 로직이 `update_member_role`과 거의
  동일하게 중복된다. 단일 PATCH가 단순.
- atomicity는 SQLAlchemy 세션이 보장: 같은 세션의 두 객체를 dirty로 만들고 단일 `commit()`
  호출하면 트랜잭션 안에서 두 UPDATE가 묶인다.

### 왜 self-leave를 별도 `/members/me`로 분리했는가
- `DELETE /members/{user_id}`는 owner가 다른 사람을 추방하는 의미고, `/me`는 본인이
  떠나는 의미. 의도 차이가 분명해 동일 엔드포인트에 self-id를 넣으면 자동 거부하는 식으로
  엮으면 클라이언트가 헷갈린다.
- FastAPI 라우트 매칭 순서: `/members/me`를 `/members/{user_id}`보다 **먼저** 선언해야
  `me` 문자열이 user_id로 잡혀 422 나는 걸 피한다.

### 왜 응답에 email을 안 넣는가
- 닉네임 검색·멤버 목록 응답에 email이 들어가면 모르는 사람의 이메일이 노출될 수 있다.
  창고 owner가 추가한 멤버라도 본인이 직접 입력한 이메일이 아니라 **검색 결과 목록**으로
  노출되는 건 기대 밖일 수 있다.
- 본인 정보는 `GET /users/me`로 별도 노출. 친구 검색·멤버 목록은 `id + nickname +
  profile_image`까지만.

### 왜 owner 본인 강등을 409로 거부하는가
- owner 1명 모델 위에서 본인 강등 = "owner 0명 상태 잠시 발생" 또는 "transfer 누락"이
  된다. 클라이언트가 의도한 건 거의 항상 transfer이므로 명시적 에러로 막고 transfer
  엔드포인트를 안내하는 게 안전.
- 자기 자신을 owner로 다시 지정하는 케이스는 멱등 no-op으로 200 반환.

### 왜 동시성 row-lock을 안 두는가
- owner는 1명뿐이라 두 owner가 동시에 다른 사람에게 transfer를 호출하는 케이스가
  애초에 발생 불가. 동일 owner가 두 클라이언트로 동시 호출하는 극히 드문 케이스만
  남는데, 이건 SQLAlchemy 세션의 기본 트랜잭션 격리(READ COMMITTED)로도 데이터
  손상은 안 난다. 명시적 row-lock(`with_for_update`)은 비용 대비 이득 없음.

### 왜 role CHECK constraint를 DB에 안 넣었는가
- 기존 컨벤션이 모델 코멘트(`models.py:75`)로만 role 값 명시, DB 강제 안 함.
- Pydantic `Literal["owner","editor","viewer"]`로 입력 보장됨.
- 추후 role 확장(예: "guest", "manager") 시 마이그 비용 발생.
- 권한 정책이 안정되면 별도 PR로 추가 권장.

## 배운 점

- **이메일 기반 친구 찾기는 카카오 OAuth와 궁합이 안 맞는다**: 카카오는 이메일 동의를
  옵션으로 두므로 미동의 시 검색 키가 사라진다. 닉네임 또는 친구 코드 등 **카카오 SDK가
  보장하는 식별자**를 검색 키로 잡는 게 안전.
- **FastAPI는 라우트를 등록 순서대로 매칭한다**: `/me`처럼 정적 path를 `/{var}`보다
  앞에 둬야 변수 캡처에 먹히지 않는다. `/members/me`를 뒤에 두면 `me`가 user_id로
  들어가 `int` 변환에서 422가 난다 (의도와 다른 응답).
- **atomic transfer는 SQLAlchemy 세션의 단일 commit으로 충분**: 두 객체를 동시에
  dirty로 만들고 한 번 commit하면 같은 트랜잭션 안에서 처리됨. 별도 `with db.begin()`
  컨텍스트 없이도 가능. autocommit이 꺼진 기본 세션에서.
- **검색 결과의 본인 제외**: 본인이 자기 자신을 친구로 추가하는 사례는 의미가 없으므로
  검색 결과에서 빼는 게 자연스럽다. 이걸 안 하면 클라이언트에서 매번 필터 처리해야 한다.

## 검증 결과 (2026-05-10 로컬, 13개 시나리오 PASS)

테스트 계정 4개 (Owner/alice/alex/Stranger)로 storage_id=15에 대해 시나리오 0~13 전부 통과.

| # | 시나리오 | 기대 | 결과 |
|---|---|---|---|
| 0 | `GET /users/search?q=al` (owner) | 200, alice/alex (본인 Owner 제외) | ✅ |
| 0a | 빈 q | 422 (Pydantic min_length) | ✅ |
| 1 | `POST /members {user_id:16, role:"editor"}` | 201, DetailResponse | ✅ |
| 2 | 미존재 user_id (99999) | 404 "사용자를 찾을 수 없습니다." | ✅ |
| 3 | 중복 추가 | 409 "이미 저장소 멤버입니다." | ✅ |
| 4 | POST role:"owner" | 422 (Literal) | ✅ |
| 5 | 멤버 목록 (alice 토큰) | 200 | ✅ |
| 6 | 비멤버 목록 (stranger) | 404 | ✅ |
| 7 | PATCH alice editor→viewer | 200 | ✅ |
| 8 | PATCH alice→owner (transfer) | 200, 기존 owner→editor 자동 강등 (atomic) | ✅ |
| 9 | owner 본인 강등 | 409 | ✅ |
| 9+ | 자기 자신 owner 멱등 | 200 no-op | ✅ |
| 10 | owner 자기 user_id 추방 | 400 + /me 안내 | ✅ |
| 11 | 멤버 추방 (DELETE) | 204 | ✅ |
| 12 | self-leave (editor alex) | 204 | ✅ |
| 13 | owner self-leave 거부 | 400 + transfer 안내 | ✅ |

소유권 이전 atomicity는 응답에 즉시 반영(`role:"owner"` 반환) + 후속 요청에서 기존 owner가 editor로 동작 확인. `/members/me` 라우트도 `{user_id}`보다 먼저 등록돼 정상 매칭됨.
