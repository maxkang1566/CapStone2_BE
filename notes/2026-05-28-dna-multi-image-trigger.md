# 2026-05-28 — 장소 공간 DNA 분석: 다중 이미지 전송 전환

## 작업 내용

`app/services/space_dna_analyzer.py`를 AI팀의 신규 엔드포인트 `POST /analyze/multi`로 전환.
이미지 URL 1장만 보내던 호출을 PlaceImage 다중 행(최대 10장)을 묶어 보내는 형태로 변경.

### 핵심 변경점

1. **엔드포인트 교체**: `/analyze/place` → `/analyze/multi`
2. **payload 키 변경**: `image_url: str` → `image_urls: list[str]`
3. **헬퍼 교체**: `_pick_image_url()` (1장) → `_pick_image_urls()` (최대 10장)
4. **상수 도입**: `_MAX_IMAGES_PER_PLACE = 10`
5. **타임아웃 증액**: `SPACE_DNA_TIMEOUT_S 120 → 180`, `SPACE_DNA_JOB_TIMEOUT_S 150 → 210`
6. **force 파라미터 추가**: `trigger_space_dna_analysis(place_id, *, force=False)`, `enqueue_space_dna_analysis(..., *, force=False)` — 백필 전용 가드 우회
7. **로깅 보강**: 호출 시점에 `place_id`, 이미지 개수, force 플래그 노출

### 신규 파일

- `scripts/_oneoff_rebackfill_space_dna_multi.py` — AI 알고리즘 업데이트 반영용 전 Place 강제 재분석 백필. `--dry-run`/`--limit`/`--via-queue` 옵션.

### 수정 파일

- `scripts/_oneoff_check_space_dna_api.py`
  - `--multi` 플래그 추가 — `/analyze/multi` dry-run
  - `--max-images` 옵션 추가
  - `required` 키 셋을 outdated 5축에서 현행 3축(`color`/`density`/`form`)으로 갱신 (2026-05-14 AI 동결 반영)

## 결정 이유 (WHY)

### 왜 다중 이미지로 전환?

- **AI팀 업데이트**: `https://hoiiiii-dna-space.hf.space/docs`의 `/analyze/multi`가 신규 개통. `image_urls: list[str]`를 받고 응답 스키마(`AnalysisResponse`)는 `/analyze/place`와 동일.
- **백엔드 자산 활용**: 캐러셀 다중 이미지 저장(2026-05-21) + 다중 장소 이미지 분류(2026-05-22)로 한 장소당 PlaceImage가 여러 행 존재. 그 중 1장만 AI에 노출되던 게 자산 낭비.
- **AI 분석 정확도**: 단일 이미지(특히 자막 + 음식 첫 컷)에 좌우되던 편향(`notes/2026-05-21-carousel-images.md` 알려진 한계)을 해소.

### 왜 상한 10장?

- 인스타 캐러셀 최대치(10)와 정합. 다중 출처(인스타 + 네이버) 누적으로 더 많아질 수는 있지만, AI 비용·지연 가드 위해 cap 도입.
- 정렬: `is_representative DESC, created_at ASC`. 대표 이미지(인스타 첫 슬라이드 / 네이버 메인) 먼저 들어가고, 그 다음 오래된 순. 다중 장소 분류 결과의 대표 이미지가 항상 1순위가 됨.
- 향후 운영 로그(평균 처리 시간·정확도)에 따라 조정. 상수 `_MAX_IMAGES_PER_PLACE`로 단일 지점.

### 왜 타임아웃 180s?

- 단일 이미지 평균 ~8s(`notes/2026-05-14-space-dna-auto-trigger.md:130`) 기준 10장 산술 80s + 모델 오버헤드. 120s는 마진이 부족할 우려.
- RQ `job_timeout`은 HTTP 타임아웃 + 30s 여유(응답 파싱·DB upsert). 180 + 30 = 210s.
- 측정 결과 안 맞으면 증액 또는 cap 하향. **검증 항목 #2 (10장 응답시간 측정)** 으로 확인.

### 왜 1장도 `/analyze/multi`로 통일?

- AI 응답 스키마가 동일 → 분기 코드 불필요. 호출부 단순.
- `image_urls=[url]` 단일 원소 배열을 AI 서버가 받아주는지는 명세상 명확하지 않아 **dry-run 검증 필수** (검증 항목 #2 추가).

### 왜 `force` 파라미터(옵션 a)인가? — `_already_analyzed` 우회 메커니즘

- **문제**: `trigger_space_dna_analysis`가 `_already_analyzed`로 skip하면 백필이 무산됨.
- **옵션 a (채택)**: keyword-only `force: bool = False`. 본체 가드 분기를 `if not force and _already_analyzed(...)`로 변경.
- **옵션 b (미채택)**: 분석 본체를 `_perform_analysis()`로 추출, 백필이 직접 호출. → 함수 분리 비용·테스트면 cleaner하나, 단일 진입점을 유지하는 게 호출처(라우터·워커)에서 추적이 쉬움.
- **호출처 무영향**: `enqueue_space_dna_analysis(place_id, queue)` 2-인자 호출 4곳(routers/instagram.py:205, 373; services/instagram_jobs.py:139; routers/storages.py:374)은 변경 없음. 기본 `force=False`로 동작 동일.
- **백필 전용**: 일상적 자동 호출은 `_already_analyzed` 가드 보호 유지. `force=True`는 backfill oneoff에서만 사용.

### 왜 outdated `required` 키 동반 갱신?

- `scripts/_oneoff_check_space_dna_api.py:104`의 `required = {"busy_calm", "calm_flashy", "modern_vintage", "premium_value", "confidence"}` 는 2026-05-14 AI가 3축(`color`/`density`/`form`)으로 동결되며 outdated 상태(`notes/2026-05-14-space-dna-auto-trigger.md:144`). 본 PR에서 `--multi` 추가하는 김에 같이 정정.

## 배운 점

- AI 알고리즘 버전 변화에 안전한 백엔드 코드 구성: `mbti_axes` 키 셋은 dict 그대로 저장(고정 스키마 안 강요)하지만, 검증 스크립트의 `required` 같은 동결 값은 노트·코드 양쪽에 흩어져 outdated되기 쉽다 → 후속으로 `dna.py`의 `AXIS_TYPES`와 단일 출처로 통합 고려.
- **검증 우선순위 — 미증명 가정 분리**: plan 단계에서 "다중 이미지 통일" 결정 후 1원소 배열 처리 가능성·10장 응답시간을 검증 항목으로 명시(plan 3중 검증 결과 반영). 가정과 검증을 분리해 적어두지 않으면 구현 후 회귀 디버깅이 어려워짐.
- **가드 우회는 함수 시그니처에 명시**: `force=True` 같은 keyword-only 파라미터는 호출처 위치 인자에 영향 없어 안전. 단일 진입점 유지하면서도 백필 같은 예외 경로 표현 가능.

## 후속 / 알려진 한계

- 이미지 추가 시 자동 재분석 훅 없음 (백로그 D2)
- 공유 창고 멤버 다중 체크인 DNA 미반영 (백로그 D3)
- `_MAX_IMAGES_PER_PLACE=10` 적정성은 운영 로그 1~2주 보고 조정
- prompt caching·결과 캐싱은 본 작업 범위 밖
- 운영 DB 재분석 백필은 사용자(운영자)가 수동 실행 — 본 PR에 자동 실행 X

## 사용자 액션

1. 배포 후 `scripts/_oneoff_check_space_dna_api.py --multi --place-id <ID>` 로 검증:
   - 다중 이미지 Place(8장+) 1건 — 응답 시간 < 180s 확인
   - 1장 Place 1건 — 단일 원소 배열 수용 확인
2. 검증 OK면 `scripts/_oneoff_rebackfill_space_dna_multi.py --dry-run` 으로 대상 수 확인
3. `scripts/_oneoff_rebackfill_space_dna_multi.py` (전체) 또는 `--via-queue`(워커 가동 시)로 풀스캔 재분석 실행
