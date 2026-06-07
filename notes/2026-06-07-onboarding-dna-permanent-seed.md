# 2026-06-07 — 온보딩 설문 DNA 평균 영구 반영 (옵션 2a → 옵션 1)

## 작업 내용

회원가입 설문(온보딩)으로 정한 초기 유저 공간 DNA가 첫 방문 장소 DNA로 사실상 덮어써지던 문제를, 설문을 평균 풀의 **동등 1표로 영구 반영**하도록 전환.

- 신규: `migrations/versions/b2e8f4a1c739_add_onboarding_axes_to_user_space_dna.py`
- 수정: `app/models/models.py`(UserSpaceDNA.onboarding_axes), `app/routers/users.py`(온보딩 POST), `app/services/user_dna.py`(rebuild + docstring)
- DB 스키마: `user_space_dna`에 `onboarding_axes JSONB NULL` 컬럼 추가

## 근본 원인 (WHY 문제였나)

온보딩 값이 `rebuild_user_dna`가 덮어쓰는 **같은 `mbti_axes` 컬럼**에 저장됐다. 옛 설계(옵션 2a)는 첫 rebuild(`total_visits==0`) 1회만 온보딩을 평균 풀에 섞었는데, 바로 그 rebuild가 `mbti_axes`를 결과 평균으로 덮어쓰면서 원본 설문값이 영구 소실됐다.

- 방문 1곳: `avg(설문, place1)` — 설문 50% (여기까진 의도대로)
- 방문 2곳~: `avg(place1, place2, …)` — **설문 영구 소실**

즉 "설문이 첫 방문에서 희석되고, 둘째 방문부터 완전히 사라지는" 구조였다.

## 결정 이유 (WHY 이렇게 고쳤나)

### 1) 별도 컬럼 `onboarding_axes` (옵션 1)
설문 원본을 rebuild가 절대 건드리지 않는 별도 컬럼에 영구 보관. `mbti_axes`(계산된 현재 DNA, 표시용)와 역할 분리. notes/2026-05-14-space-dna-onboarding-post.md가 이미 "영구 보존은 옵션 1(별도 컬럼 + 마이그레이션)"로 예고했던 경로.

### 2) 동등 1표 영구 반영 (사용자 선택)
설문을 평균 풀에 매 rebuild마다 1건으로 포함. 방문이 N곳이면 설문 가중치는 1/(N+1)로 **자연 감쇠하되 0이 되지 않는다**. 사용자가 "초기 설문 DNA와 방문 장소 DNA의 평균"이라 표현했고, 가중치 모델 질문에서 "동등 1표(영구)"를 명시 선택(방문 9곳 → 설문 10% preview 확인). 고정 비중/가중 평균은 의도적으로 채택 안 함.

### 3) nullable, server_default 없음
`onboarding_axes`는 nullable. 온보딩 안 한 유저(NULL)와 완료(값)를 구분한다. `mbti_axes`의 `server_default='{}'`와 의도적 차이 — "빈 설문" vs "설문 안 함" 구별. rebuild의 `if existing.onboarding_axes:` 가드가 NULL/빈 dict를 모두 안전 처리.

### 4) total_visits는 방문 수 그대로
온보딩은 평균 풀에 들어가지만 `total_visits` 카운트에는 미포함. `n_spots`를 온보딩 append 전에 계산해 "방문 spot 수"라는 사용자 노출 의미를 보존.

### 5) 온보딩 POST upsert에 onboarding_axes 추가, where는 무수정
`.values()`와 `.on_conflict_do_update(set_=)` 양쪽에 `onboarding_axes=axes` 추가. `where=mbti_axes='{}'` 가드는 그대로 — 신규 INSERT는 충돌 없으면 성공, 충돌 시에만 where 평가하므로 3분기(신규 성공 / AI빈행 UPDATE / 이미온보딩 409)가 정확히 유지. `onboarding_axes IS NULL` 조건 추가는 불필요.

### 6) 백필: 복구 가능 코호트만
`total_visits=0 AND mbti_axes != '{}'`인 유저는 아직 첫 rebuild 전이라 mbti_axes에 원본 설문이 남아 있다 → onboarding_axes로 복사. `total_visits>0`은 옛 rebuild가 이미 평균으로 덮어 복구 불가(WHERE 가드로 평균값을 설문으로 오인 복사하는 것 차단). 운영 데이터 적재 직전이라 영향 미미.

## 부수 기록

- **record_history_for_spot**: 스냅샷이 이제 항상 온보딩 섞인 평균을 담지만, history 정의("그 시점의 유저 DNA")가 곧 온보딩+방문 평균이므로 정합. 별도 수정 안 함.
- **AI 키셋 리스크**: place DNA의 AI 키셋이 `{color,density,form}`을 벗어나면 `_average_axes` 공통 키 교집합이 줄어 온보딩 기여가 왜곡될 수 있음. 기존 옵션 2a에도 있던 리스크이고 신규 설계가 악화시키지 않음. AI팀이 3축 키 유지하는 한 문제없음.

## 배운 점

1. **덮어쓰는 컬럼에 영구 보존 값을 같이 두면 안 된다.** 온보딩(영구)과 계산 결과(휘발)가 같은 `mbti_axes`를 공유한 게 근본 원인. 수명주기가 다른 데이터는 컬럼을 분리해야 한다.
2. **upsert의 where 절은 충돌 시에만 평가된다.** 신규 INSERT 경로는 where와 무관하게 성공하므로, `values`에 새 컬럼을 추가해도 신규 유저는 정상 저장. 처음엔 where에 `onboarding_axes IS NULL`을 더해야 하나 고민했지만 불필요했다(검증으로 확인).
3. **검증은 로컬 Docker DB에서.** DATABASE_URL이 운영 Supabase를 가리키고 있어, 스키마 변경 검증은 `$env:DATABASE_URL`을 로컬로 오버라이드(load_dotenv는 override=False라 환경변수가 우선)해 진행. 운영 DB 무영향.
4. **self-contained oneoff**: 임시 유저/저장소/장소/스팟을 만들어 검증 후 전부 삭제. `uq_spots_storage_place`(같은 storage+place 1 spot) 때문에 A/B를 각자 storage로 분리해야 했다.

## 검증 결과 (로컬 Docker DB)

- 마이그레이션 upgrade → onboarding_axes 생성, downgrade -1 → 제거, 재 upgrade 왕복 OK.
- oneoff 7항목 ALL PASS:
  1. 온보딩 → onboarding_axes 저장 + total_visits=0
  2. 온보딩 재호출 → 409
  3. X visit → avg(설문, X), total_visits=1, X 단독과 다름
  4. Y visit → avg(설문, X, Y), 설문 기여 지속, total_visits=2
  5. history snapshot = 그 시점 mbti_axes
  6. X,Y unvisit → 설문 단독 복귀, total_visits=0
  7. 온보딩 없는 유저 → place 단독 (NULL onboarding 안전)

## 후속 / 미해결

- **배포 시 운영 DB 마이그레이션 수동 실행**: Railway는 `alembic upgrade head` 자동 실행이 없음(Dockerfile은 uvicorn 직실행). 운영 Supabase에 수동 적용 필요 — **마이그레이션 먼저, 코드 배포 나중**(모델이 onboarding_axes 참조하므로 컬럼 없으면 쿼리 에러).
- **복구 불가 코호트**: 이미 방문 이력 있는 기존 유저는 원본 설문 소실 + 온보딩 POST `where mbti_axes='{}'` 가드가 재온보딩을 409로 막음. 운영 데이터 적재 전이라 별도 처리 안 함.
