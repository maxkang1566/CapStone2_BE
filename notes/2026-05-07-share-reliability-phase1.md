# 2026-05-07 — /instagram/share 신뢰성 보강 Phase 1

## 작업 내용

`/instagram/share` 자동 매핑 흐름의 두 가지 신뢰성 구멍을 메운다.

1. **네이버 Local Search 실패가 `not_a_place_post`로 위장되던 문제 수정** — 키 누락·네트워크 오류·4xx/5xx 응답을 빈 리스트로 swallow 하던 동작을 `NaverLocalSearchError`로 전환. 라우터에서 502로 매핑해 외부 의존성 장애를 클라이언트가 명확히 인지하도록 한다. 부분 실패도 전체 실패로 취급(일부 후보만 검색돼 자동 매핑이 오탐할 위험 차단).
2. **`instagram_post_cache`의 restricted_page 응답이 영구 캐시되던 문제 수정** — Apify가 인스타 차단으로 `error: "restricted_page"` 페이로드를 반환하면 그게 그대로 영원히 캐시 hit이라, 인스타가 차단을 풀어도 빈약한 데이터가 계속 반환된다. restricted 캐시에만 6시간 TTL을 적용해 자연스럽게 재크롤되도록 한다. 정상 캐시는 만료 없음(보수적, 사용자 결정 (c)).

수정 파일:
- `app/services/naver_local_search.py` — 빈 리스트 swallow → `NaverLocalSearchError` raise 4건 (키 누락, 네트워크, non-200, JSON 파싱)
- `app/services/instagram_pipeline.py` — `_RESTRICTED_TTL_SECONDS` 상수, `_is_restricted_expired` 헬퍼, `get_cached`에서 만료된 restricted 캐시는 None 반환, `save_cache`를 PostgreSQL UPSERT로 변환(`fetched_at` 갱신 보장)
- `app/routers/instagram.py` — `share_instagram_post`에서 `NaverLocalSearchError` 502 매핑

## 결정 이유 (WHY)

### 왜 일부 후보 실패도 전체 실패로 묶는가
share_post는 추출된 후보 N개를 Naver에 순차 검색해 결과를 합친 뒤 unique dedupe로 자동 매핑 분기를 결정한다. 후보 일부만 성공하면:
- 살아남은 결과로 unique=1이 되면 **잘못된 가게에 자동 저장**될 수 있음 (못 본 후보가 다른 매칭을 가져왔을 가능성)
- unique=0이면 사용자에게 "장소 게시물이 아닙니다"라고 거짓말

→ 자동 매핑의 정확도 정책("유니크 1건 수렴할 때만 저장")을 깨지 않으려면, 외부 검색이 한 건이라도 실패하면 전체 거절이 안전.

### 왜 502인가 (504/503 아님)
- 502 Bad Gateway: 게이트웨이로 동작하는 우리 서버가 업스트림(네이버) 응답을 정상적으로 받지 못함을 정확히 표현.
- 503은 우리 서비스 자체 다운, 504는 타임아웃 한정. 4xx 키 오류·5xx·네트워크를 모두 502 한 코드로 묶는 게 단순.
- 504(타임아웃)만 별도 분리할 만한 가치는 낮음(클라이언트 동작 동일).

### 왜 정상 캐시에는 TTL 안 거는가
- 인스타 캡션은 거의 수정되지 않음(편집은 가능하나 드물고, 편집해도 보통 오타 정정 수준)
- TTL을 모든 캐시에 적용하면 정상 캐시도 6시간 후 재크롤 → 불필요한 Apify 호출 비용 증가
- restricted_page는 인스타가 시기적으로 차단을 풀고 잠그는 패턴이라 명백히 재시도 가치 있음

### 왜 `save_cache`를 UPSERT로 바꾸는가
TTL 만료된 restricted 캐시 행을 None 반환하면 fetch_post가 다시 Apify를 호출한다. 그 결과를 `save_cache(INSERT)`로 저장하려 하면 PK(shortcode) 충돌로 IntegrityError → rollback → **새 데이터가 저장되지 않음(기존 restricted 값이 그대로 남음)**. PostgreSQL `ON CONFLICT DO UPDATE`로 UPSERT 처리해 만료 후 재크롤된 결과로 행을 덮어쓴다. `fetched_at`도 `NOW()`로 갱신해 다음 만료 체크 기준점이 정확해진다.

### 왜 마이그레이션 없이 가능한가
스키마 변경 없음. `fetched_at`은 이미 모델에 존재하고 `server_default=func.now()`로 INSERT 시 자동 채움. UPSERT 시에는 `set_={'fetched_at': func.now()}`로 명시 갱신.

## 배운 점

- **휴리스틱 흐름의 "부분 결과"는 자동 결정에 독약**: unique 1건 수렴 정책처럼 "결과 집합의 카디널리티"로 분기하는 로직은 빠진 결과 한 건이 분기를 뒤집는다. 외부 의존성 실패는 잘못된 자동 결정보다 사용자에게 솔직히 알리는 게 낫다.
- **PostgreSQL UPSERT(`on_conflict_do_update`)는 SQLAlchemy의 dialects 모듈 임포트 필요**: 일반 `insert`가 아니라 `from sqlalchemy.dialects.postgresql import insert`. 이 프로젝트는 Postgres 고정이라 이식성 걱정 없음.
- **silent fallback은 디버깅 두통**: naver_local_search의 빈 리스트 swallow는 처음엔 "안전한 기본값"처럼 보였지만, 실제로는 키 만료·네트워크 단절·할당량 초과를 모두 같은 신호("후보 없음")로 묻어버려 역추적이 어려웠다. 도메인 로직이 외부 호출의 성공/실패를 구분하는 게 의미 있을 때는 raise가 맞다.
