# 2026-05-14 — 공간 DNA 자동 분석 트리거

## 작업 내용

새 Place 저장 시 AI팀 외부 분석 API(`https://hoiiiii-dna-space.hf.space/analyze/place`)를
RQ 잡으로 호출해 `place_space_dna` + `place_tags`를 자동 채우는 흐름 도입.

- **신규**: `app/services/space_dna_analyzer.py` (enqueue + trigger + tag 재구축),
  `scripts/_oneoff_check_space_dna_api.py` (dry-run),
  `scripts/backfill_space_dna.py` (백필),
  `scripts/_oneoff_dna_count.py` (검증)
- **수정**: `app/routers/instagram.py` (`/save` + `/share` 캐시 hit 두 곳에 enqueue),
  `app/services/instagram_jobs.py` (캐시 miss 워커 분기에 enqueue),
  `app/services/user_dna.py` (`_is_valid_axes` 키 셋 강제 제거 + `rebuild_user_dna` 공통 키 평균),
  `.env.example` (`SPACE_DNA_API_URL` 추가)

## 결정 이유 (WHY)

### 1. dry-run으로 잡힌 AI 응답 스키마와 plan 가정의 불일치
**선택: AI 응답 그대로 받음, `_is_valid_axes` 키 셋 강제 제거**.
- 핸드오프 doc(`notes/2026-05-09-ai-team-handoff.md` §5)은 5축(busy_calm/calm_flashy/
  modern_vintage/premium_value/confidence, -1~1)을 권장하고 합의 미정이라고 명시.
- 실제 AI 응답: **3축 (color/density/form, 0~70 범위) + dna_code: "SMV"** + ai_summary +
  top_tags. AI팀이 합의 없이 다른 스킴으로 갔거나 운영자에게 통보 안 됨.
- 4축 5키를 강제하던 `_is_valid_axes`로는 모든 응답이 invalid → place_space_dna 영영
  비어 + user_dna 평균도 영영 빈 채로 굳음.
- 키 셋 강제를 풀고 "dict이고 모든 값이 number"로 일반화. `rebuild_user_dna`도 모든
  valid row의 공통 key 셋만 평균에 사용 → AI 스킴 변경에 강건.

### 2. RQ 잡 사용(BG task가 아님)
**선택: RQ 잡으로 enqueue**.
- 기존 `place_enrichment.enqueue_blog_fetch_job`이 같은 위치에서 같은 RQ 패턴. 컨벤션 일치.
- 외부 API 호출 120초가 BG(워커 스레드 점유)보다 RQ(잡 가시성·실패 registry·타임아웃 분리)에
  적합. 워커가 동시에 share 잡(180s) 처리해도 DNA 잡은 별도 슬롯에서 진행.

### 3. enqueue 위치 — service 아닌 router/worker에서
**선택: `/save` 핸들러, `/share` 캐시 hit 분기, `process_share_job` 워커 3곳에서 직접 enqueue**.
- 3중 검증에서 service(spot_creator)에 BackgroundTasks/RQ Queue import는 layering 역전이라고
  지적. 코드베이스 컨벤션(`spots.py:135`, `instagram.py:318`)이 "service entrypoint만
  export, enqueue는 web 계층".
- `share_post`도 시그니처 안 건드림 — 호출자가 `result.spot.place_id`(`ShareResult.spot`)로
  추출해 직접 enqueue.

### 4. 트리거 조건 — "saved 분기 무조건" + 워커 본체에서 멱등 가드
**선택: `result.status=="saved" and result.spot is not None`이면 무조건 enqueue,
워커 본체에서 `_already_analyzed` 체크 후 skip**.
- 사용자 결정: 새 Place + DNA 없는 기존 Place 모두 트리거. `place_created` 분기 검사하지
  않음. 중복 enqueue는 워커 본체의 `_already_analyzed` 가드로 무해.
- `_is_valid_axes(row[0])`로 가드 → "분석 시도했지만 invalid응답"인 경우 다시 시도할 수 있게.

### 5. AI 응답 검증 — invalid면 upsert 스킵
**선택: `_is_valid_axes` 통과한 경우만 upsert, invalid면 ERROR 로그 + upsert 안 함**.
- 빈 `mbti_axes={}` 저장하면 백필 쿼리(LEFT JOIN psd.place_id IS NULL)가 실패분 자동 누락.
  upsert 자체를 스킵하면 row가 안 생기므로 같은 쿼리가 자동 재처리.

### 6. `top_tags` 키 — dry-run으로 `tag_name` 확정
**선택: 단일 키 `tag_name`, 점수 `score`. fallback 없음**.
- 초기 plan은 `name`/`tag` 추측 fallback이었으나 3중 검증에서 "추측은 누락 위험"이라고 지적.
- dry-run 실측으로 `tag_name` 확정 → 단일 키로 고정. 추측 코드 제거.

### 7. `tags` 마스터 sanitize
**선택: `1<=len<=30` + 빈 차단 리스트 hook**.
- AI 응답 임의 문자열이 globally 노출되는 `tags` 마스터에 INSERT되므로 운영 리스크.
  최소 가드 + `TAG_BLOCKLIST` set만 마련(v1 분석에서 부적절 태그 관측되면 채움).

### 8. `place_tags` 재구축 — DELETE 후 INSERT
**선택: 같은 잡 트랜잭션 내 `delete + add`**.
- 재분석 시 태그 변경 정합성 보장. 동시 첫 분석 race window는 `_already_analyzed` skip이
  거의 모든 케이스에서 차단 — 첫 30초 내 동시 두 번째 enqueue 한정.

### 9. AI API가 자체적으로 DB write함, 그래도 우리도 upsert
**선택: 응답 받은 후 우리 백엔드도 upsert**.
- dry-run 검증: AI API가 응답 반환 직후 `place_space_dna`에 행이 이미 생김.
- 우리도 같은 데이터로 upsert하면 idempotent — 외부 시스템 동작이 바뀌어도 우리 DB는 우리가 보장.
- `updated_at`만 한 번 더 갱신되는 정도라 큰 부담 없음.

### 10. 동반 구현 — 백필 스크립트
**선택: 본 작업에 포함, PR 머지 직후 1회 실행이 완료 조건**.
- 트리거는 spot 저장 시점에만 발동. 시드 21건 중 spot이 한 번도 안 만들어진 Place는 영영
  비어있는 채. 백필 동반 구현이 운영 안정성 필수.
- 재실행이 곧 resume — 쿼리가 처리분을 자동 제외.

## 배운 점

- **외부 협업 인터페이스 동결 전 가정의 비용**: 핸드오프 doc에 "권장 키 명세 + 합의 필요"가
  적혀 있었지만 합의 단계 없이 코드 작성하면 실제 응답으로 잡힐 때 user_dna 평균 같은
  하부 시스템이 통째로 깨짐. dry-run을 plan 첫 단계에 못박은 이번 흐름이 그걸 잡았다.
- **콘솔 출력 mojibake ≠ DB mojibake**: Windows PowerShell이 CP949라 한글 태그가 깨져
  보이지만 `place_tags.tag_name`은 UTF-8 정상. SELECT로 직접 보면 정상. 운영 검증할 때
  "콘솔 출력이 깨졌으니 DB도 깨졌겠지"라고 단정하지 말고 Supabase Studio 같은 UTF-8 환경에서
  재확인.
- **`limit(1001)` 트릭은 핀에만, 백필은 ORDER BY id**: 핀은 truncation 감지가 목적이라
  1001 trick이 맞지만 백필은 결정론적 진행이 중요해서 `ORDER BY Place.id.asc()`로 고정.
  부분 실행 후 재실행해도 같은 순서로 이어짐(처리분이 LEFT JOIN으로 자동 제외).
- **워커 inline 회피의 가치**: 처음 plan은 `instagram_jobs.py:108` 워커가 share_post 후
  inline 분석(120s 추가 블로킹). 3중 검증에서 "share 잡 timeout=180s에 한계 근접"으로 지적.
  같은 RQ 큐에 별도 잡으로 분리 → 워커 단일 슬롯에서도 share/dna가 직렬 처리되어 각자
  timeout 안에서 안전.

## 검증 결과

### dry-run (운영 Supabase, 읽기만)
- `place_id=14` → 500 "cannot identify image file" (대표 이미지가 만료된 인스타 CDN URL,
  핸드오프 doc §7에 기록된 이슈)
- `place_id=15` → 200 OK, 14.8s. 응답:
  ```json
  {
    "place_id": 15,
    "dna_code": "SMV",
    "mbti_axes": {"density": 24.79, "color": 25.8, "form": 25.15},
    "top_tags": [{"tag_name": "...", "score": 1.0}, ...],
    "ai_summary": "분석 결과 SMV 공간의 부드러운 느낌입니다.",
    "updated_at": "2026-05-13T15:44:00.502489"
  }
  ```
- 호출 직후 `SELECT * FROM place_space_dna WHERE place_id=15` → 행 자동 생성 확인
  (**AI API가 자체적으로 supabase에 write**).

### 백필 실행 (운영 DB)
- 31건 발견 (시드 21 + 추가 10) → 22건 성공, 8건 PlaceImage 없음 skip, 1건 만료 URL 500.
- 결과:
  ```
  places total           = 33
    with PlaceImage      = 25
    with place_space_dna = 24
    missing DNA          = 9   (8 no-image + 1 expired-url)
  mbti_axes keys observed: {'form': 24, 'color': 24, 'density': 24}
  tags master = 24, place_tags rows = 72
  ```
- 분석 1건당 평균 ~8초(warm).

### 스모크
- `from app.services import space_dna_analyzer` 단독 import OK.
- `import app.main` 포함 전체 라우터 부팅 OK.
- enqueue 자체는 RQ 워커가 떠 있어야 검증 가능 — 운영 Railway에서 RQ worker가 떠
  있는지 별도 확인 필요.

## 후속 / 미해결

- **이미지 없는 Place 8건**: 인스타 raw_payload['images']에서 다시 끌어와 PlaceImage로
  올려야 함. 핸드오프 doc §6의 "이미지 영구 저장 마이그레이션" 백로그와 동일.
- **만료된 인스타 URL Place 1건 (id=14)**: 같은 게시물 재시딩 또는 Supabase Storage로
  마이그레이션 후 재분석.
- ~~mbti_axes 스킴 통일~~: **2026-05-14 동결 — color/density/form 3축** (`notes/2026-05-09-ai-team-handoff.md` §5 갱신).
- **AI팀 Supabase Studio 초대 vs 외부 API 직접 호출 흐름의 공존**: 응답 데이터를 AI가 자체
  write + 우리도 upsert하는 이중 쓰기 — 향후 AI팀이 API만 호출하고 우리가 write로 단일화하면
  더 깔끔.
- **클라이언트 폴링/푸시 정책**: v1은 detail 진입 시 `GET /places/{id}/space-dna` fetch.
  분석 미완료면 has_data=false. polling 또는 SSE는 별도 plan.
- **`/instagram/share` 캐시 hit 분기**: 본 작업에서 enqueue 추가했으나 실서비스 통합
  검증(클라이언트가 실제 게시물 공유 → 자동 분석 결과 확인) 미수행 — Railway 배포 후 검증.
- **place_tags score 정규화/임계값**: 현재 raw score(0~1 범위)를 그대로 저장. 임계값
  미만 태그 제외, 정규화는 후속.
