# 2026-05-07 — /instagram/share 비동기화 + 비용 가드 통합 (Phase 2)

## 작업 내용

`/instagram/share`가 새 URL일 때 5~30초 동기 블로킹하던 문제를 해결한다. `/instagram/crawl-async`와 동일한 하이브리드 패턴을 적용:

- **캐시 hit** → 지금처럼 즉시 결과(`saved` / `needs_selection` / `not_a_place_post`)
- **캐시 miss** → `job_id` 즉시 반환, 백그라운드 워커가 `share_post` 실행. 클라이언트는 `GET /instagram/share-jobs/{id}` 폴링.

## 데이터 흐름

```
POST /instagram/share { url, storage_id? }
  ├─ 캐시 hit: 기존 share_post 흐름 즉시 수행 → status="done", result에 결과
  └─ 캐시 miss:
      1. instagram_crawl_jobs 행 생성 (kind='share', user_id, storage_id 저장)
      2. RQ 큐에 process_share_job(job_id) 등록
      3. 즉시 status="pending", job_id 반환
      [워커 프로세스]
      4. 잡 행에서 url/user_id/storage_id 로드
      5. instagram_share.share_post() 호출
      6. 결과를 잡 행 payload에 직렬화 → status="done"
GET /instagram/share-jobs/{job_id}
  → 잡 행 status/payload/error 그대로 반환
```

## 결정 이유 (WHY)

### 왜 단일 엔드포인트(/share)에서 hit/miss 분기인가
- `/crawl-async`가 이미 같은 패턴(hit이면 result, miss면 job_id) → 일관성
- 클라이언트가 한 엔드포인트만 알면 됨 (학습 비용↓)
- 캐시 hit 케이스(같은 URL 재공유, 다른 사용자가 이미 본 URL 등)는 폴링 비용 0

### 왜 새 테이블(`InstagramShareJob`) 안 만들고 기존 잡 테이블 재사용하나
- 컬럼이 70% 겹침 (id, status, source, payload, error, created_at, completed_at)
- 마이그레이션 1개로 끝 (kind/user_id/storage_id만 추가)
- **비용 가드(#10)가 자동 통합**: 가드는 `jobs.source='apify' AND created_at>=this_month` 카운트로 동작. share 잡도 같은 테이블에 들어가니 별도 처리 불필요.

### 왜 잡 dedupe(=같은 URL 빠르게 두 번 누른 경우 중복 잡 방지)를 일단 안 하는가
- Spot에 `UniqueConstraint('storage_id', 'place_id')`가 이미 있어 결과적으로 한 번만 저장됨
- 두 잡이 동시에 끝나도 두 번째는 IntegrityError → spot_creator 안에서 already_saved 처리됨
- dedupe는 정합성 문제가 아니라 비용/UX 문제 → MVP 후순위

### 왜 user_id/storage_id를 잡 행에 저장하나
- 워커는 라우터의 `current_user`를 못 봄 (다른 프로세스, 요청 컨텍스트 없음)
- 잡 등록 시 직렬화해 두면 워커가 DB에서 읽어 그대로 share_post에 넘길 수 있음
- crawl 잡에는 nullable (의미 없음)

### 왜 `kind` 컬럼이 필요한가
- 같은 테이블에 두 종류 잡이 섞이면 `/instagram/jobs/{id}`(crawl 폴링)와 `/instagram/share-jobs/{id}`(share 폴링)가 잘못된 잡을 반환할 수 있음
- 라우터에서 `kind`로 필터해 wrong-type 접근을 404로 막음

### 왜 폴링 엔드포인트는 분리(`/share-jobs/{id}`)하나
- 응답 모델이 다름: crawl 잡은 `InstagramCrawlResponse`, share 잡은 `InstagramShareResponse`
- 한 엔드포인트에서 두 타입을 union으로 반환하면 OpenAPI 스키마가 모호해지고 클라이언트 코드 분기 부담↑

## 변경 파일

- `app/models/models.py` — `InstagramCrawlJob`에 `kind`(default='crawl'), `user_id`(nullable), `storage_id`(nullable) 컬럼 추가
- `migrations/versions/<new>.py` — Alembic 마이그레이션
- `app/schemas/instagram.py` — `InstagramShareEnqueueResponse`, `InstagramShareJobStatusResponse` 추가
- `app/services/instagram_jobs.py` — `process_share_job(job_id)` 워커 함수 추가
- `app/routers/instagram.py`:
  - `POST /instagram/share` — 캐시 hit/miss 분기로 재작성
  - `GET /instagram/share-jobs/{id}` — 신규 폴링 엔드포인트
- `claude-progress.txt` — Phase 2 항목 추가

## 비용 가드 통합 (#10) 설명

기존 `_is_apify_budget_exceeded`는 변경 없음:
```python
count = jobs.filter(source='apify', created_at>=month_start).count()
estimated_cost = count * _ESTIMATED_APIFY_COST_PER_CALL
```

share 잡도 워커가 Apify 호출 후 `source='apify'`로 마킹하니 자동으로 카운트에 포함됨. 별도 카운터 테이블·코드 추가 불필요.

## 짚고 넘어갈 점

### 동기 fallback은 안 두는가?
캐시 miss 시 항상 비동기. 초기 안에서는 "캐시 hit이면 동기, miss면 비동기"라는 단순 규칙. 모바일 클라이언트 입장에서는 `status` 필드만 확인하면 분기 처리 가능.

### 워커가 죽으면 잡이 영구 pending 됨
RQ는 잡 timeout(현재 180초) 후 워커가 실패로 마킹하긴 하지만, 워커 자체가 죽어 있으면 큐에 남기만 함. 실서비스에서는 워커 헬스 모니터링이 별도 과제(Phase 2 범위 밖).

### 인증 토큰을 워커에 안 넘기는 이유
JWT 토큰을 잡 행에 저장하면 보안 리스크. 대신 user_id만 저장하고 워커는 그 ID로 User 객체를 직접 로드 — 권한은 storage_id+user_id 조합으로 spot_creator가 검증.

## 배운 점

- **테이블 합치기 vs 분리하기 결정 기준**: 컬럼이 70% 이상 겹치고 외부 카운트(비용 가드)가 같은 테이블을 보고 있으면 합치는 게 자연. 30% 미만 겹치면 분리. 이번 케이스는 합치는 쪽.
- **워커 인증 컨텍스트 직렬화**: 요청 시점의 사용자 정보를 워커에 넘기려면 ID 단위로만 (토큰·세션 객체 X). 워커가 DB에서 다시 로드.
- **응답 모델 분기는 status 필드 하나로**: 캐시 hit/miss를 하나의 응답 모델 안에서 status='done'|'pending'으로 표현하면 타입은 단순해지고 OpenAPI도 명확해진다(union 대신 optional 필드).
