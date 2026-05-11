# 2026-05-11 — 유저 DNA 자동 업데이트 트리거

## 작업 내용

스팟 방문 체크인(`is_visited` 토글) 또는 visited 스팟 소프트 삭제 시 사용자의 `user_space_dna`를 자동 갱신하는 백그라운드 트리거를 구현. MVP 4종 중 마지막 미구현 항목.

- **신규**: `app/services/user_dna.py` (rebuild + history upsert + BG 진입점), `scripts/_oneoff_check_user_dna.py`
- **수정**: `app/routers/spots.py` PUT/DELETE 두 핸들러에 변화 감지 가드 + `BackgroundTasks.add_task`
- **마이그레이션 없음**: 기존 `user_space_dna` / `user_space_dna_history` 모델 그대로 사용

## 결정 이유 (WHY)

### 1. BackgroundTasks vs 동기 inline vs RQ
**선택: BackgroundTasks**.
- DNA 계산은 외부 I/O 없음 (SQL JOIN 1회 + Python dict 평균). RQ 인프라(Redis 큐, 워커, 잡 행)는 과잉.
- 동기 inline은 PUT 응답을 직접 늘려 모바일 체크인 UX에 영향. 사용자는 응답 후 즉시 화면 이탈.
- BG는 Redis 미가동·워커 미가동에도 작동. MVP 6일 일정에 적합. 누락 우려 있으면 RQ로 승격은 Phase 2.

### 2. Incremental running average vs Rebuild from scratch
**선택: Rebuild from scratch**.
- `is_visited`는 PUT으로 false 토글 가능 + `Spot.deleted_at` 소프트 삭제 존재 → incremental은 역연산이 사실상 불가능. 데이터 드리프트 누적.
- 사용자당 visited 스팟은 수십~수백 추정. 단일 JOIN + Python `sum()/n`이면 ms 단위. 매 체크인마다 rebuild해도 여유.
- `total_visits`도 같은 쿼리의 row count로 동시 산출 → "현재 평균을 만든 스팟 수"라는 의미가 일관됨.

### 3. 결측치(빈 mbti_axes / 키 일부 누락) 처리
**선택: spot 단위 전체 제외**.
- 부분 평균은 축마다 분모(N)가 달라져 비교성·재현성을 깨고 디버깅을 어렵게 함.
- `mbti_axes={}`는 `place_space_dna`의 `server_default='{}'` — AI팀 미분석 시그널이라 평균 오염 방지가 안전.

### 4. 트리거 가드 — 모든 PUT마다 재계산하지 않기
**선택: `body.is_visited != prev_visited` 변화 감지**.
- 사용자가 메모/평점만 바꿀 때 DNA 재계산은 낭비.
- visit↔unvisit 양방향 모두 트리거해야 정합성 유지 (단방향 가드는 unvisit 무시).

### 5. 공유 창고에서 added_by ≠ visitor
**선택: `Spot.added_by == current_user.id`인 경우만 트리거**.
- 현 스키마는 visitor를 따로 저장 안 함. PUT은 owner/editor 누구나 가능 → 어느 사용자 DNA에 반영해야 하는지 모호.
- `added_by` 기준이 가장 모호함 적음 (자신이 추가한 것만 자신의 DNA에 반영).
- 멀티 멤버 체크인은 별도 모델링 필요 — Phase 2.

### 6. UserSpaceDNAHistory.spot_id unique=True 의미 재해석
**선택: `ON CONFLICT (spot_id) DO UPDATE`**.
- unique 제약을 "이 spot이 DNA에 기여한 가장 최근 스냅샷 1행"으로 해석.
- unvisit→재visit 시 update가 자연스러움. 멱등성은 같은 트랜잭션 재시도 시만 필요하므로 upsert로 충분.

### 7. BackgroundTask 안의 DB 세션
**선택: 새 `SessionLocal()` 컨텍스트**.
- 라우터의 `db` 세션은 요청 종료(get_db의 finally) 시 close됨. BG에 그대로 넘기면 사용 시점에 닫힌 세션.
- `app/routers/places.py:22-94`(네이버 블로그 enrichment)는 BG 함수가 자체적으로 세션을 열도록 설계 — 동일 패턴 따름.

## 배운 점

- **PUT에 is_visited 외 다른 필드 수정 시 트리거 안 하기**는 단순 가드 한 줄(`!= prev_visited`)로 깔끔히 해결. 처음엔 "토글 진입 시점만 트리거" 식의 복잡한 상태 머신을 고려했지만, 변화 감지 한 줄이 가장 단순하고 옳음.
- **PostgreSQL JSONB 빈 dict 비교는 SQL에서 까다로움** (`!= '{}'::jsonb` 필요). 어차피 키 존재 검증을 Python에서 해야 하므로, SQL 필터는 최소화하고 Python에서 `_is_valid_axes`로 통합 — 코드가 한 곳에 모이고 디버깅 쉬움.
- **BackgroundTasks의 함수는 picklable이 아니어도 됨** (RQ와 달리). 같은 프로세스 스레드풀에서 실행되므로 `SessionLocal` 같은 모듈 전역 객체를 그대로 사용 가능. RQ로 승격하면 함수 경로 문자열 + 직렬화 가능 인자만 허용되는 제약이 다름.
- **`UserSpaceDNAHistory.spot_id` unique 제약**은 처음 봤을 때 "snapshot 누적 이력" 의도와 충돌해 보였지만, 사용자당 visited 스팟이 한정적이라는 전제 하에서 "이 spot의 최신 기여 시점" 모델로 해석하면 자연스러움. 시간 축 추적이 정말 필요해지면 별도 `_at`별 행으로 스키마 진화.

## 검증 결과 (운영 Supabase, 읽기+rebuild만)

- `place_space_dna` 0행, `user_space_dna` 0행, visited spots 0건 — AI팀 데이터 적재 전이라 평균 계산 로직 자체는 미검증
- user_id=3에 `rebuild_user_dna` 강제 실행 → 0건 합산이라도 예외 없이 `total_visits=0, mbti_axes={}` 행 upsert 확인 (안전성 검증 통과)
- **회귀 발견·수정**: 행이 있고 `mbti_axes={}`인 케이스에서 `GET /users/me/space-dna`와 `GET /places/{id}/space-dna`가 `has_data=true, mbti_axes=null` 응답하던 inconsistency. 5/10 정책("빈 데이터는 has_data=false")에 맞춰 `if not dna or not dna.mbti_axes:` 가드로 통일. P0 트리거가 모든 첫 체크인 사용자에게 빈 행을 만들기 시작하므로 이번에 묶어 수정.

## 후속 / 미해결

- **end-to-end 평균 계산 검증**: AI팀이 Supabase에 `place_space_dna.mbti_axes != '{}'` 행을 적재한 뒤 시드 사용자가 그 장소들에 visit 체크인 → 4축 평균이 의미 있는 값인지 확인 필요
- **클라이언트 UX**: PUT 응답에는 `total_visits` 갱신값이 안 들어감 (BG는 응답 후 실행). 클라이언트가 즉시 DNA 화면 이동 시 stale 데이터 가능 — 1~2초 후 재조회 또는 "DNA 분석 중" 표시 필요 여부 클라이언트 팀과 협의
- **자동 테스트 부재**: 회귀 방어를 BG 트리거 가드 변경 시 어떻게? P4 인프라 백로그(/tests + ruff)와 묶어서 처리
