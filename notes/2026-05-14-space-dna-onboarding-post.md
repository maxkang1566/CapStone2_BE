# 2026-05-14 — 공간 DNA 온보딩 POST 엔드포인트 도입

## 배경

신규 가입자는 `user_space_dna` 행이 없어 `GET /users/me/space-dna` 응답이 항상 `has_data=false`. `rebuild_user_dna` 자동 트리거는 첫 spot 방문 체크인 후에야 행을 만든다. 그 사이 사용자는 추천·피드에서 콜드 스타트 상태. 프론트엔드 팀이 회원가입 직후 16개 온보딩 질문으로 3축 비율을 사전 수집하기로 기획 확정 → 백엔드 POST 엔드포인트 신설.

## 결정 사항 (WHY 중심)

### 경로 = `POST /users/me/space-dna`

기존 GET과 같은 리소스. "온보딩"은 호출 시점일 뿐 리소스 정체성이 아니므로 `/onboarding/` prefix를 도입하지 않았다. 후속 온보딩 필드가 늘어나면 그때 재구조화.

### HTTP 메서드 POST + 상태 201

PUT은 멱등 = "재호출 = 덮어쓰기" 의미가 따라붙는데 정책상 재호출은 409로 막아야 하므로 POST가 정확. 201은 행이 "처음 의미 있는 데이터로 생성된 시점"이라는 의미에서 INSERT/UPDATE 양쪽에 모두 적용 가능.

### 입력 스키마 = 중첩 dict + 합=100 검증

사용자가 명시: "각 축의 두 반대 유형 점수 합이 100." 단일 값(한쪽 비율) 표현도 가능하지만 의미 명확성 우선해서 `{axis: {type_a: x, type_b: 100-x}}` 채택. 합=100 검증은 `±0.01` 허용(프론트 부동소수점 누적 오차 대비). 키는 정확히 `{color, density, form}` × 각 축의 유형 키도 정확히 일치 — superset/subset 모두 거부. 잘못된 키가 들어오면 GET 정규화와 후속 `rebuild_user_dna`의 공통 키 평균이 오염되니 백엔드 1차 방어.

### 영어 키 = 기존 AI 응답 키 유지 (`color/density/form`)

축 명칭 매핑 — AI팀 확인 (2026-05-14):

| 영어 키 | 한글 라벨 | 두 유형 |
|---|---|---|
| `color` | 자극 강도 | `high` ↔ `mild` |
| `density` | 분위기 밀도 | `dense` ↔ `sparse` |
| `form` | 트렌디함 | `fresh` ↔ `vintage` |

새 한글 명칭이 들어왔을 때 새 영어 키를 도입할지 고민했으나, 기존 AI 응답(`color/density/form` — 2026-05-14 동결)과 키를 일치시키면 AI 마이그레이션 불필요 + `user_dna.py` 평균 로직 수정 불필요 → 작업 범위 최소. 한글 라벨은 클라이언트 측에서 매핑.

### 재호출 정책 = 최초 1회만 (409)

이미 `mbti_axes`가 비어있지 않은 행이면 409 Conflict. 사용자가 명시한 정책. PUT으로 두면 의미가 흐려지므로 POST + 409 분기.

### 동시성 = ON CONFLICT DO UPDATE WHERE + RETURNING

```sql
INSERT INTO user_space_dna (...) VALUES (...)
ON CONFLICT (user_id) DO UPDATE SET ...
WHERE user_space_dna.mbti_axes = '{}'::jsonb
RETURNING user_id, total_visits
```

단일 SQL로 세 분기를 원자 처리:
- (a) 행 없음 → INSERT → RETURNING 1행 → 201
- (b) 빈 행({}) → UPDATE → RETURNING 1행 → 201
- (c) 채워진 행 → WHERE false → silently skip → RETURNING 0행 → 409

PostgreSQL row lock으로 멀티 디바이스 동시 POST race 안전. SELECT-then-INSERT 패턴은 두 요청 모두 SELECT 시점에 "행 없음"을 본 뒤 INSERT 충돌이 발생하는 race window가 존재 → 채택 안 함.

### `total_visits` UPDATE에서 의도적 제외

AI 자동 트리거가 PlaceSpaceDNA 0건이라도 `total_visits=n` 행을 먼저 만들 수 있다(`user_dna.py:56-78`). 그 카운트를 온보딩이 0으로 죽이면 안 됨 → UPDATE의 `set_`에서 제외해 기존 값 보존. RETURNING은 UPDATE *후* 값을 반환 → set_에 미포함이라 그 값이 그대로 응답에 반영.

### GET 응답 정규화 = 옵션 C (백엔드 단일 지점 변환)

AI 트리거는 `{color: 25.8, ...}` 단일 값으로 저장하고 온보딩은 `{color: {high, mild}, ...}` 중첩 dict로 저장 → GET 응답에서 두 형태가 시점에 따라 혼재. 클라이언트가 두 형태 모두 처리(옵션 A)는 부담. AI 트리거를 중첩 dict로 마이그레이션(옵션 B)은 AI팀 합의 + `user_dna.py` 평균 로직 수정 + 마이그레이션 필요해서 MVP 일정 위협. 백엔드의 GET 핸들러에서만 단일 값 → 중첩 dict로 변환하는 옵션 C 채택 — `_normalize_axes_to_pairs` 헬퍼.

변환 룰: AI 단일 값은 AXIS_TYPES의 첫 요소(예: `color`의 `high`) 비율을 의미한다는 AI팀 확인을 따라 `{type_a: v, type_b: 100-v}`로 펼친다. 향후 AI팀이 직접 중첩 dict로 저장하기 시작하면 헬퍼의 dict 분기로 자연 흡수.

응답 값은 모두 `round(value, 2)`로 반올림한다 — `100.0 - 24.79 = 75.21000000000001` 같은 IEEE 754 차연산 누적 오차가 응답에 새는 것을 백엔드 단일 지점에서 차단. 모바일 클라이언트의 `toFixed` 처리 부담 제거.

### AI 트리거와의 관계 — 옵션 2a로 정정

plan 단계 초기에는 "AI 트리거가 온보딩 값 덮어써도 OK"로 결정했으나, 구현 후 사용자가 동작을 재검토해 **옵션 2a (첫 방문에만 온보딩 평균 풀에 포함)**로 변경. plan 결정 #2(덮어쓰기 OK)는 폐기.

**옵션 2a 동작**:
- `rebuild_user_dna` 시작 시 현재 user_space_dna 행 조회 → `is_first_rebuild = existing is not None and total_visits == 0`
- 첫 rebuild인 경우 `_flatten_onboarding_axes`로 온보딩 중첩 dict를 AI 응답과 동일한 단일 값 형태로 평탄화 (각 축 첫 유형 비율 추출, AXIS_TYPES 따라)
- 평탄화된 온보딩 1건을 valid 풀에 append → spot DNA들과 함께 단순 평균
- `total_visits`는 spot visit 수만 카운트 (온보딩은 visit이 아니므로 별도)
- 두 번째 rebuild부터는 `total_visits > 0`이라 온보딩 풀 포함 분기 자동 스킵

**평탄화 룰**:
- 중첩 dict: `{color: {high: 60, mild: 40}}` → `{color: 60}` (첫 유형 비율)
- 이미 단일 값: 그대로 통과 (AI 트리거가 만든 행이 이미 단일 값일 수 있음)
- 미지 축의 중첩 dict: 무시 (AI 키 변경 호환)
- 미지 축의 단일 값: 통과

**시나리오 정합성 검증** (`scripts/_oneoff_check_user_dna_option2a.py`):
- A. 신규+spot1 → spot1 그대로
- B. 온보딩+첫 visit → (온보딩 + spot) 평균
- C. 둘째 visit → spot 둘 평균 (온보딩 빠짐)
- D. 온보딩+0 visit → 평탄화된 온보딩 그대로
- E. 행 없음+0 visit → 빈 dict (`has_data=false` 유도)

`user_dna.py` 수정: `_flatten_onboarding_axes` + `_average_axes` 헬퍼 추출, `rebuild_user_dna`에 첫 rebuild 분기 추가. 마이그레이션 없음.

## 변경 사항

- `app/schemas/dna.py`: `Field`/`field_validator` import 추가. `REQUIRED_AXES`, `AXIS_TYPES`, `SUM_TOLERANCE` 상수. `UserSpaceDNAOnboardingRequest` 모델 (validator 포함).
- `app/routers/users.py`: `status`/`cast`/`JSONB`/`pg_insert`/`datetime`/`timezone` import 추가. `_normalize_axes_to_pairs` 헬퍼 (단일 값/중첩 dict → 응답용 중첩 dict, `round(value, 2)`). GET 핸들러 docstring 정정(4축→3축) + 정규화 적용. `create_my_space_dna` POST 핸들러.
- `app/services/user_dna.py` (옵션 2a 적용): `AXIS_TYPES` import. `_flatten_onboarding_axes`, `_average_axes` 헬퍼 추가. `rebuild_user_dna`에 `is_first_rebuild` 분기 추가 — 첫 rebuild에 한해 온보딩 평탄화 후 평균 풀에 1건 append.
- `docs/API_SPECIFICATION.md`: 사용자 라우트 테이블에 POST 행 추가. GET 명세를 3축 동결 + 정규화 규칙으로 업데이트. POST 명세 신설. Place DNA 섹션 stale "MBTI 4축" 표현도 정정.
- 마이그레이션 없음 — JSONB 컬럼 그대로 사용.

## 미수행 / 후속

- 클라이언트(모바일팀) 가이드: `is_new_user=true` 흐름에서만 POST 호출하도록. 백엔드는 409로 이중 방어.
- AI 단일 값의 "한쪽 비율 의미" 가정은 AI팀 회신에 기반. 향후 AI팀이 중첩 dict로 직접 저장하기 시작하면 `_normalize_axes_to_pairs`의 dict 분기로 자연스럽게 흡수됨.
- `rebuild_user_dna`도 중첩 dict로 마이그레이션(옵션 B)은 MVP 이후 검토.
- 단위 테스트는 `/tests` 디렉토리 부재로 미작성 — 별도 인프라 작업.
