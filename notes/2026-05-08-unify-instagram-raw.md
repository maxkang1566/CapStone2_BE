# 인스타 raw → place_raw_data 1차 소스 통합

작성일: 2026-05-08
관련 마이그레이션: d4f9a1b3c5e7

## 작업 내용

인스타 공유 흐름의 데이터 적재 구조를 다음과 같이 정리했다.

**Before**:
- Apify 응답은 `instagram_post_cache`(shortcode PK)에 저장
- spot_creator에서 `place_raw_data(provider='instagram', provider_place_id=NULL)`에 `{url, caption, thumbnail_url}` 축약본을 별도 INSERT
- 사용자에게 보여줄 caption은 raw_payload JSONB 안에만 있고 spots에 노출 안 됨

**After**:
- Apify 응답이 처음부터 `place_raw_data(provider='instagram', provider_place_id=shortcode, place_id=NULL)`에 전체 payload로 저장
- spot_creator는 같은 shortcode의 raw 행을 찾아 `place_id` UPDATE (조건부, race 안전)
- caption은 `spots.caption` 컬럼으로 정제 노출 → `SpotResponse`에 직접 실림

## 결정 이유 (WHY)

### 왜 1차 소스 일원화가 필요했나
사용자 요구가 정리되면서 분명해졌다: "장소 저장 시 raw → 정제 → 사용자 표시" 흐름을
명확히 분리하고 싶다고. 그런데 인스타 raw가 두 테이블에 분산돼 있으면 어느 것이
"진짜 1차 소스"인지 모호하다. instagram_post_cache는 캐시 의도였지만 사실상
place_raw_data의 full payload보다 더 많은 정보를 들고 있었다(축약본은 정보 손실).

### 왜 instagram_post_cache 폐지인가
대안은 (a) 폐지 vs (b) shortcode → raw_data_id FK만 들고 있는 가벼운 룩업 테이블로 축소.
- `place_raw_data`에 이미 부분 유니크 인덱스 `(provider, provider_place_id) WHERE
  provider_place_id IS NOT NULL`이 있어 lookup은 O(log n) 보장됨 → 별도 캐시 테이블 불필요.
- 통합하면 코드 경로 단순화 (한 테이블만 알면 됨).
- 손실 없음: 메타키 `_source`/`_url`을 raw_payload에 주입해 보존.

### 왜 spots.caption은 denormalize인가
caption을 raw_payload JSONB 파싱으로 매번 꺼내는 건 (a) 응답 시 매번 JOIN+파싱,
(b) raw_payload 구조 변경 시 응답 코드도 영향. caption은 인스타 게시물별로 변하지
않는 immutable 값이라 중복 저장 부담이 거의 없다. → spots에 컬럼으로 빼는 게 단순.

### 왜 cascade='save-update, merge'로 좁혔나
기존 `cascade="all, delete-orphan"`은 자식 행이 association을 끊으면 즉시 DELETE.
`place_id` nullable이 되면서 raw가 정상적으로 place_id=NULL 상태로 존재할 수 있게
됐는데, 누가 `place.raw_data` 컬렉션을 조작하면 의도치 않은 삭제가 발생할 수 있다.
DB FK의 `ON DELETE CASCADE`는 그대로 살아있어 place 삭제 시 raw 정리는 보장됨.

## 마이그레이션 주의사항

### 실행 전
- **RQ 워커를 반드시 중지**해야 한다. 실행 중이면 워커가 구 instagram_post_cache에
  INSERT하는 동시에 마이그레이션이 DROP을 시도해 race가 발생할 수 있다.
- 마이그레이션은 transactional DDL이지만 데이터 손실 가능성이 있는 단계가 있어
  사전 백업 권장.

### Downgrade 한계
- spots.caption 컬럼 DROP은 백필된 caption 데이터 손실
- place_id IS NULL 행은 NOT NULL 복원 위해 DELETE
- 마이그 전 상태로 정확히 복원되지 않음(축약본 행이 영구 손실됨)

### regexp_match 트릭
SQLAlchemy `text()`가 `:name` 패턴을 bind parameter로 인식해 `(?:p|reel|tv)` 같은
정규식의 비-캡처 그룹이 깨진다(`:p`가 bind로 해석됨). regexp_match로 우회하면서
capturing group 인덱스(`[2]`)로 shortcode를 추출하는 방식 사용.

## 배운 점

1. **부분 유니크 인덱스의 conflict target**: `ON CONFLICT (col1, col2) WHERE
   <predicate> DO UPDATE`에서 WHERE 절이 인덱스 정의와 정확히 일치해야 함.
2. **ON CONFLICT DO UPDATE의 set_에 무엇을 빼느냐가 중요**: 1차 소스 통합처럼
   "재크롤이 정제 연결을 보존해야 하는" 케이스에서 place_id를 set_에서 빼면
   기존 연결이 안전하게 유지된다.
3. **SQLAlchemy text()의 bind parameter 인식**은 raw SQL에 콜론이 등장할 때마다
   주의가 필요. 특히 정규식. 회피 방법은 (a) 다른 함수로 우회 (b)
   exec_driver_sql 사용 (c) 콜론을 동적 concat.
