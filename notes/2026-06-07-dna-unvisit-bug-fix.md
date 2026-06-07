# 2026-06-07 — Unvisit 시 유저 공간 DNA 미반영 버그 수정

## 작업 내용

스팟 `is_visited`를 True→False로 토글해도 사용자의 `user_space_dna`에서 해당 장소의 DNA가 평균 풀에서 빠지지 않던 회귀 버그 수정. 한 줄 변경.

- 수정: `app/services/user_dna.py:106` `rebuild_user_dna` 평균 풀 필터
  - 변경 전: `Spot.visited_at.is_not(None)`
  - 변경 후: `Spot.is_visited.is_(True)`
- 수정: `scripts/_oneoff_check_user_dna.py:55` 동일 필터 동기화
- 노트/progress 기록

마이그레이션 없음. 신규 엔드포인트/스키마 없음.

## 결정 이유 (WHY)

### 1) 버그 원인

PUT 핸들러(`app/routers/spots.py:122-123`)는 `is_visited=True`로 토글될 때 `visited_at`을 한 번 자동 세팅하지만, `is_visited=False`로 되돌릴 때 `visited_at`을 **clear하지 않는다** — 첫 방문 시각 메타데이터를 보존하려는 의도된 동작.

한편 `rebuild_user_dna`는 평균 풀 후보를 `Spot.visited_at.is_not(None)`로 필터하고 있었다. 결과: 한 번이라도 visit 됐던 spot은 `visited_at`이 남아 있어 unvisit 후에도 평균에 계속 포함됨. PUT의 BG 트리거(`spots.py:130-137`)는 정상적으로 돌고 있었으나 rebuild 쿼리가 같은 row를 다시 집계하니 결과가 변하지 않았다.

### 2) 왜 `is_visited` 필터로 바꿨나 (visited_at clear 대안 기각)

- `is_visited`가 PUT이 직접 조작하는 토글의 **source of truth** 컬럼. PIN 조회(`spots.py:268`), DELETE 가드(`spots.py:159`)도 이미 `is_visited` 기준.
- `visited_at`은 "첫 방문 시각" 메타데이터 성격 — 토글마다 비웠다 세우면 사용자가 "이 장소 처음 갔던 게 언제더라"를 추적할 수 없게 된다.
- 두 컬럼의 역할 분리 유지: `is_visited`=토글 상태, `visited_at`=첫 방문 기록.
- 한 줄 변경, 마이그레이션 불필요.

### 3) 검증 시나리오 (3중 적대 검증 결과 추가)

V2 검증에서 단일 토글 외에 다음 시나리오 추가:
- **멀티 spot unvisit**: X, Y 둘 다 visit → X만 unvisit → 평균이 Y 단독으로 줄어들고 `_average_axes`의 공통 키 교집합 재계산이 올바른지.
- **트리거 가드 회귀 방지**: visit된 spot에 `user_memo`만 수정 (is_visited 미포함) → BG 트리거 안 돌아야 함 (`body.is_visited != prev_visited` 가드).

### 4) 안 한 것

- `spots.is_visited` 인덱스 추가 — `added_by` FK가 충분히 selective(사용자당 visited 수십~수백 추정). 운영 데이터 적재 후 EXPLAIN 보고 별도 PR.
- 과거 stale `user_space_dna_history` 정리 — 사용자가 재토글하면 자연스럽게 갱신됨.
- PUT의 `visited_at` 자동 세팅 로직은 그대로 유지.

## 배운 점

1. **토글 컬럼과 시각 메타데이터가 분리된 스키마에서는 집계 쿼리가 항상 토글 컬럼을 봐야 한다.** 둘이 같은 정보를 다른 형태로 가졌다고 가정하면 PUT/PATCH가 한쪽만 갱신할 때 desync가 발생한다.

2. **버그 트리거는 도는데 결과가 안 바뀐다**는 보고가 들어오면 트리거 조건이 아니라 **재계산 로직 자체의 입력**을 봐야 한다. `spots.py:130-137`의 토글 감지는 양방향 모두 잡고 있었지만, rebuild 쿼리가 잘못된 컬럼을 보고 있어서 BG가 "같은 데이터로 같은 평균"을 다시 계산하고 있었다.

3. **3중 적대 검증의 가치**: V1(코드 정확성), V2(시나리오 커버리지), V3(부작용 감사)를 병렬로 돌려서 빠뜨릴 뻔한 시나리오 2건(멀티 spot, 트리거 가드 회귀)을 검증 항목에 포함시킬 수 있었다.
