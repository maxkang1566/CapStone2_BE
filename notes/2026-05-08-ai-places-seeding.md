# 2026-05-08 — AI팀용 운영 Places 시딩 도구 도입

## 작업 내용

AI팀이 공간 DNA(MBTI 4축) 분석을 개발·검증하려면 운영 Supabase에 의미 있는 분량의 Place + 리뷰 + 이미지 + raw_payload가 채워져 있어야 한다. 현재 운영 DB는 거의 비어 있고 시딩 도구도 없어 진척이 막힌 상태였다. 이번에 시딩 스크립트와 입력 템플릿, 가이드 문서를 도입했다.

추가/수정 파일:
- 신규: `scripts/seed_places.py` — 시드 계정으로 운영 API를 호출해 Place·Spot·리뷰를 채우는 메인 스크립트
- 신규: `seeds/instagram_urls.txt` — 인스타 URL 리스트 (사용자가 100건 큐레이션)
- 신규: `seeds/places_naver_fallback.csv` — 네이버 메타 폴백 양식
- 신규: `seeds/golden_set.csv` — AI팀 검증용 골든셋(10건) 양식
- 신규: `seeds/README.md` — 시딩 작업 가이드 (사전 셋업 / 실행 / 검증 / AI팀 인터페이스)
- 신규: `notes/2026-05-08-ai-places-seeding.md` — 본 메모

## 결정 이유 (WHY)

### 왜 시딩 도구를 새로 만들었나
- AI팀은 기획상 DB에 직접 접근해 `place_space_dna`에 결과를 쓴다(`notes/2026-05-03-planning-decisions.md`).
- 하지만 운영 DB가 비어 있으면 분석 자체가 시작 불가. 사람이 손으로 Place 100건을 만드는 건 비현실적이고, alembic 시딩 마이그레이션은 운영 데이터 변경이라 부담.
- **이미 동작 중인 운영 API를 호출**하는 스크립트가 가장 안전·재현가능한 경로 — 멱등성, 검증 로직, 백그라운드 리뷰 수집까지 그대로 재사용.

### 왜 `/instagram/share`를 메인 경로로 잡았나
- Apify 적용(`notes/2026-05-06-apify-instagram-pipeline.md`) 이후 인스타 URL 1개로 Place + 두 종 raw_data + 인스타 이미지 + Spot이 한 번에 만들어지고, RQ 워커가 비동기로 네이버 블로그 리뷰까지 적재.
- 인스타 캡션·해시태그가 보존되어 raw_payload에 남으니 멀티모달 DNA 분석에 더 풍부한 입력.
- `/places/from-naver`만으로 가면 인스타 캡션·이미지 데이터가 없어 분석 입력이 빈약.

### 왜 폴링 로직을 스크립트에 넣었나
- 첫 검증 때 `/share`를 단순 동기 호출로만 가정했으나, **실제로는 cache hit만 동기, miss는 `job_id` 반환 후 `/instagram/share-jobs/{id}` 폴링**이라는 사실을 코드 재확인에서 발견.
- 시딩 100건은 모두 첫 처리라 cache miss가 기본. 폴링이 없으면 plan 자체가 안 굴러감.

### 왜 dry-run을 20~30건으로 늘렸나
- 5건은 saved/needs_selection/not_a_place_post 비율을 결정할 표본으로 부족. plan 검증 라운드에서 표본 확대 권고가 나왔다.
- saved 비율이 50% 미만이면 메인을 네이버 직입력 경로로 격하해야 인스타 위주 시딩이 무너지지 않음.

### 왜 RQ 워커 운영 점검을 사전 셋업 0번으로 끌어올렸나
- `Dockerfile`은 `uvicorn` 단일 CMD라 워커는 자동으로 안 뜬다. `docker-compose.yml`에선 `--profile worker`로 옵트인.
- 운영 Railway에 워커 프로세스가 있다는 보장이 코드에 없으니, 시딩 시작 전 반드시 확인하지 않으면 share 잡 100건이 전부 pending에서 멈춘다.

### 왜 `place_space_dna` 키 명세를 plan에 박아뒀나
- 모델은 `mbti_axes JSONB server_default='{}'`로 키 강제 없음(`models.py:206`). 메모는 한국어로만 4축 설명.
- AI팀과 합의 없이 시작하면 키 이름·값 범위가 갈린다. plan v3에 영문 키와 -1.0~1.0 범위 권장값을 박아두고, 합의 후 본 메모에서 동결한다.

### 왜 다중 이미지 안내를 별도로 박았나
- Apify는 `images` 배열을 다중으로 돌려주지만 `instagram_share.py:90`에서 `images[0]`만 thumbnail로 추출 → `place_images`에는 1장만 저장.
- 나머지는 `place_raw_data(provider='instagram').raw_payload`에 보존. 멀티모달 분석을 하려면 AI팀이 raw_payload 쪽을 파싱해야 한다는 사실을 명시.

## 배운 점

- **계획 검증을 두 관점(실행 가능성 + 사용성)으로 나눠 돌리니 누락이 잘 잡힌다**: 한 번에 한 관점만 보면 폴링 누락이나 워커 운영 같은 차단성 이슈가 묻혀버린다. 메인 plan + 별도 검증 라운드의 패턴은 이번처럼 외부 의존이 많은 작업에 특히 유효.
- **하이브리드 sync/async API는 외부 호출자에게 폴링 부담을 떠넘긴다**: 시딩 같은 일회성 스크립트도 폴링·타임아웃·실패 분기를 모두 처리해야 한다. 클라이언트(모바일)도 이 흐름을 매번 다뤄야 하므로, 단순 시딩이라도 production-grade 재시도/로깅이 필요.
- **PostGIS POINT의 lat/lng 추출**: AI팀에 SQL을 전달할 때 `coordinate`를 그대로 두면 GeoAlchemy 응답이 와서 다루기 어려움. `ST_X(coordinate::geometry)` / `ST_Y(...)` 로 풀어주는 게 친절. README에 미리 박음.
- **본문/스니펫 길이 분포 확인이 중요**: 네이버 블로그 리뷰 본문 추출 실패 시 스니펫 fallback이라 텍스트 길이가 천차만별. AI팀이 임계값 합의 없이 분석하면 스니펫만 들어온 Place에서 noise가 커진다. 검증 SQL에 `length(text)` 분포 포함.

## 후속

- 사용자가 `seeds/instagram_urls.txt`에 100건을 큐레이션하고, RQ 워커 운영 셋업·환경변수 등록·시드 계정 가입을 마치면 dry-run 시작.
- saved 비율 측정 후 본 메모에 결과 추가 (메인 경로 인스타 유지/네이버 격하 결정).
- AI팀 합의 후 `place_space_dna` 키 명세 동결.
- 분포 매트릭스(category_group × 행정구 × review_count_bucket)는 시딩 완료 후 별도 첨부.

## 진척 (2026-05-08 — 사전 셋업 완료)

- Railway RQ 워커 운영 정상 확인 (Replica 1, Apify→네이버 매핑→블로그 enrichment 파이프라인 풀스택 실측).
- 옛 잔존 데이터 정리 완료 (트랜잭션 + 검증 쿼리로 안전 삭제):
  - `places`: 11→1 (id=14 다케오 호르몬 데판야끼 보존)
  - `place_raw_data`: 24→3 (id=14의 naver/instagram/naver_blog), 고아 행 4개도 정리
  - `place_reviews`: 25→10 / `place_images`: 8→1 / `instagram_crawl_jobs`: 14→0
  - 정리 스크립트: `scripts/_oneoff_cleanup_preview.py`, `scripts/_oneoff_cleanup_execute.py`
- 시드 계정 셋업 완료:
  - `test@example.com` (user.id=13) 이미 가입돼 있어 비번을 `test1234`로 강제 갱신 (`scripts/_oneoff_reset_seed_password.py`)
  - owner storage.id=13 ('내 저장소') 자동 생성된 상태 그대로 사용
  - 운영 API `/auth/login` 응답 200 + bearer 토큰 정상 발급 검증 완료
- 다음: `seeds/instagram_urls.txt`에 dry-run 25건 큐레이션 → `poetry run python scripts/seed_places.py --mode share --limit 25` → saved 비율 측정.
