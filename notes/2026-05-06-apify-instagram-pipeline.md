# Apify 기반 Instagram 크롤링 파이프라인 도입 (2026-05-06)

## 작업 내용

기존 비로그인 Playwright(OG 메타) 크롤러로는 위치 태그 없는 게시물의 본문·장소 정보를 거의 추출 못 하는 한계가 있어, Apify 액터(`apify/instagram-post-scraper`)를 **기본 크롤러**로 도입하고 OG 크롤러는 fallback으로 유지.

### 신규/변경 파일

**생성**
- `app/services/apify_client.py` — Apify HTTP 클라이언트 (run-sync-get-dataset-items 사용)
- `app/services/instagram_pipeline.py` — shortcode 추출, 캐시 조회, Apify→OG fallback, 응답 정규화
- `app/services/instagram_jobs.py` — RQ 잡 함수 `process_crawl_job(job_id)`
- `app/worker.py` — RQ 워커 엔트리포인트
- `migrations/versions/xxxx_add_instagram_apify_tables.py` — 신규 테이블 2개 생성

**수정**
- `app/models/models.py` — `InstagramPostCache`, `InstagramCrawlJob` 모델 추가
- `app/routers/instagram.py` — `POST /instagram/crawl-async`, `GET /instagram/jobs/{id}` 신규
- `app/schemas/instagram.py` — Apify 응답 필드(hashtags/lat/lng/posted_at) + `InstagramJobResponse`
- `pyproject.toml` — `apify-client`, `rq` 의존성 추가
- `.env.example` — `APIFY_TOKEN`, `APIFY_INSTAGRAM_ACTOR_ID`, `REDIS_URL`, `APIFY_MONTHLY_BUDGET_USD`
- `app/main.py` — Redis 연결 초기화
- `docker-compose.yml` — RQ worker 서비스

---

## 결정 이유 (WHY)

### Q. 왜 Apify인가? (자체 구축 vs 다른 서비스)
- **자체 로그인 농장 운영**은 운영 부담·계정 정지 리스크 + 주거용 프록시 비용으로 MVP에 부적합
- **RapidAPI 영세 스크래퍼**는 운영자 신뢰성 부족 (갑작스런 종료/품질 저하)
- **Bright Data**는 엔터프라이즈 가격(월 $500+)으로 오버킬
- **Apify**는 게시물당 $0.001 수준 + 액터가 여러 개라서 하나 깨져도 갈아탈 수 있음 + Python 클라이언트 공식 제공

### Q. 왜 URL 단위 캐시를 두는가?
- 인스타 게시물 URL = 사실상 불변(post shortcode 기반)
- 같은 게시물을 여러 사용자가 공유해도 Apify 호출은 1번이면 충분
- 앱 사용자 늘수록 캐시 히트율 상승 → 비용 곡선 완만

### Q. 왜 캐시를 Redis가 아닌 DB 테이블에 저장하는가?
- 영구 보존 가치 있음 (1년 뒤 재공유돼도 캐시 효과 유효)
- Redis는 영속화 미설정 + TTL 관리가 오히려 복잡
- JSONB로 디버깅·재처리 용이
- 향후 Apify 응답을 분석/재가공할 때도 DB 쿼리로 접근 가능

### Q. 왜 비동기 처리(RQ + 잡 폴링)인가?
- Apify 호출이 5~20초 걸려서 동기 응답으로 쓰면 모바일 UX 망가짐
- Redis는 이미 docker-compose에 떠있어서 RQ 채택 시 신규 인프라 0
- Celery는 모니터링·운영 복잡도 대비 MVP에 과함
- FastAPI BackgroundTasks는 서버 재시작 시 작업 손실 → 잡 결과를 신뢰할 수 없음

### Q. 왜 OG 크롤러를 제거하지 않고 fallback으로 유지하는가?
- Apify 장애 시 서비스 다운 방지
- `APIFY_MONTHLY_BUDGET_USD` 한도 초과 시 자동 전환 가능 → 비용 폭주 가드
- 위치 태그가 박힌 게시물은 OG로도 충분 — 이런 케이스를 무료로 처리하면 비용 절감 가능 (단, 최초 도입 단계에서는 일관성 위해 Apify 우선)

### Q. 왜 잡 상태를 별도 테이블(`instagram_crawl_jobs`)에 두는가?
- 클라이언트가 폴링할 수 있어야 함
- RQ 자체 상태(Redis)는 휘발성이라 잡이 끝난 뒤 결과 조회 어려움
- DB에 저장하면 동일 URL 재요청 시 캐시 hit이면 잡 자체를 만들지 않고 즉시 응답 가능

### Q. 왜 `/instagram/save`는 그대로 두는가?
- 방식 C 결정 유지 — 장소 매핑은 네이버 검색으로만
- 클라이언트는 `crawl-async` → 잡 결과 받기 → 네이버 검색 → `/save` 흐름
- save는 이미 `place_raw_data`에 Instagram 풀 페이로드를 저장하는 구조라 변경 불필요

---

## 데이터 흐름

```
1. POST /instagram/crawl-async {url}
     → shortcode 추출 (ex: "C1abc23xyz")
     → instagram_post_cache 조회
        - hit → 즉시 정규화된 응답 반환 (job 생성 안 함)
        - miss → instagram_crawl_jobs 행 생성(status=pending) + RQ enqueue → {job_id} 반환

2. [Worker] process_crawl_job(job_id)
     → 이번 달 Apify 호출 카운트 체크 (instagram_crawl_jobs.payload->>'__source'='apify' 카운트)
        - 한도 초과 → OG fallback 경로
     → Apify 호출 시도
        - 성공 → instagram_post_cache 저장(source='apify')
        - 실패 → InstagramCrawler.crawl_post() (OG fallback) → instagram_post_cache 저장(source='og_fallback')
     → instagram_crawl_jobs 업데이트(status=done, payload=정규화된 응답)

3. GET /instagram/jobs/{job_id}
     → status: pending | done | failed
     → done이면 result 포함

4. (이후) POST /instagram/save  → 변경 없음
     → place_raw_data(provider='instagram', raw_payload=Apify 풀 응답)
     → place_images(source='instagram') 다중 이미지
     → spots
```

---

## 배운 점 / 주의사항

- **Python 3.14 + apify-client**: 2026년 1월 기준으로 apify-client는 3.14를 공식 지원. 만약 이슈가 있으면 httpx로 직접 Apify REST API(`POST /v2/acts/{actor}/run-sync-get-dataset-items?token=...`)를 호출해도 동일하게 동작.
- **Apify run-sync vs run**: MVP는 `run-sync-get-dataset-items` (동기 호출, 결과 즉시 반환) 사용. 워커가 어차피 비동기 컨텍스트라서 굳이 polling 안 해도 됨.
- **Windows 로컬 RQ 워커**: RQ는 fork 기반이라 Windows에서는 SimpleWorker나 `--worker-class rq.SimpleWorker` 필요. README/스크립트에 명시.
- **비용 가드의 한계**: `instagram_crawl_jobs`로 카운트하는 방식은 정확하지 않음(액터별 가격 다를 수 있음). 정확한 사용량은 Apify 콘솔에서 모니터링 필요. MVP 가드용으로만 활용.
- **Apify 응답 정규화 키 매핑**: `caption`, `displayUrl`, `images`, `videoUrl`, `locationName`, `locationId`, `latitude`, `longitude`, `hashtags`, `mentions`, `timestamp`, `ownerUsername` — 액터 버전 따라 키가 미묘하게 다를 수 있어 `dict.get()` 안전 접근.

---

## 미해결 / 향후 개선

- **푸시 알림**: 현재는 폴링 방식. 향후 FCM 등 푸시 도입 시 잡 완료 즉시 통지 가능
- **부분 실패 재시도**: 워커에서 Apify 일시 장애 시 N회 재시도(지수 백오프) — RQ retry 옵션 활용
- **이미지 저장**: Apify가 주는 `images` URL은 인스타 CDN 직접 링크라 만료될 수 있음. 향후 S3/Supabase Storage로 미러링 고려
- **샵박스 정리**: `og_fallback`으로 저장된 캐시는 데이터 빈약 → 사용자가 "다시 시도" 버튼 누르면 Apify로 재시도하는 옵션 제공 가능
