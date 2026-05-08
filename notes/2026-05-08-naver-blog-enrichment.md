# 네이버 블로그 enrichment — RQ 잡 기반 본문 수집 (2026-05-08)

## 작업 내용

- 신규 서비스 4개:
  - `app/services/naver_blog_search.py` — 네이버 Blog Search API 래퍼 (Local Search 패턴 미러)
  - `app/services/naver_blog_body_fetcher.py` — 모바일 블로그 페이지 본문 크롤러 (httpx + BS, 모바일 UA)
  - `app/services/place_enrichment.py` — enqueue + freshness/quota 가드 + 검색→본문→DB 적재 오케스트레이션
  - `app/services/queue.py` — 워커 잡 함수에서 RQ Queue 핸들 얻는 헬퍼
- 워커 확장 (`app/services/instagram_jobs.py`):
  - `process_blog_fetch_job` 신규 — payload['place_id'] → `fetch_and_persist_blog_reviews`
  - `process_share_job` 끝부분에 enrichment enqueue 추가 (best-effort)
- 라우터 수정 (`app/routers/instagram.py:save_instagram_spot`):
  - Spot 생성 성공 후 enqueue (best-effort, 큐 미초기화 시에도 본 응답 그대로)
- 환경변수 3개 추가 (`.env.example`):
  - `NAVER_BLOG_MONTHLY_QUOTA_CALLS`
  - `NAVER_BLOG_BODY_FETCH_SLEEP` (기본 0.5)
  - `NAVER_BLOG_BODY_MAX_CHARS` (기본 2000)
- DB 마이그레이션 없음. `place_raw_data` / `place_reviews` 기존 멀티 프로바이더 스키마 그대로 사용.

## 결정 이유 (WHY)

- **왜 별도 RQ 잡(`kind='naver_blog_fetch'`)인가**: share 잡 latency를 늘리고 싶지 않다. 블로그 수집은 사용자 UX와 무관하고 AI 학습 데이터용이라 share 폴링 결과에 포함될 필요 없음. 또한 분리하면 실패 시 재시도/재수집 스크립트도 만들기 쉬움.

- **왜 30일 갱신**: 항상 갱신은 쿼터 낭비, 영구 캐시는 신선도 부족. 30일은 리뷰 트렌드 변동 주기상 합리적 절충점. AI 팀 요청 시 환경변수로 조정 가능하게 분리해뒀음.

- **왜 `/save`도 트리거**: needs_selection 응답 후 사용자가 수동으로 고른 케이스도 동일하게 새 Place가 만들어지는데, 자동 저장만 enrichment 받으면 데이터셋이 비대칭으로 비어버림.

- **왜 본문 크롤링까지 하는가**: AI 팀이 자체 모델을 학습 중이라 200자 × 10건 = 2KB 스니펫은 학습 코퍼스로 빈약. 본문 2,000자 × 10건 ≈ 20KB/place로 다축 분류 모델에 의미 있는 신호 확보.

- **왜 모바일 페이지인가**: 데스크톱 `blog.naver.com`은 본문이 `PostView.naver` iframe 안이라 헤드리스 브라우저(Playwright) 없이 못 긁음. `m.blog.naver.com`은 직접 HTML 렌더라 httpx + BeautifulSoup으로 충분 → Playwright 부담·차단 위험 둘 다 회피.

- **왜 첫 2,000자**: 광고/협찬 인삿말이 본문 후반에 몰려 앞쪽 신호 비율이 높음. 자체 모델이 토크나이저 단에서 어차피 길이 정규화하니 백엔드는 양 확보에 집중.

- **왜 fallback 패턴 (본문 → 스니펫)**: 차단·셀렉터 실패 시에도 분석은 돌아야 함. 스니펫이라도 남기면 분석 가능. 길이로 필터링은 AI 단에서.

- **왜 `payload ? 'inserted_reviews'`로 쿼터 카운트**: 원래 플랜의 `status='done'`만 보면 `_should_refresh=False`로 스킵된 잡(API 호출 0회)도 카운트돼 한도가 부당하게 일찍 닫힘. 실제 외부 호출이 있었던 행만 세야 한다.

- **왜 `_build_query`에서 시/도 토큰을 빼는가**: 한국 주소 첫 두 토큰은 보통 "서울특별시 영등포구"라 첫 토큰이 검색 정확도를 깎는다. 시/도 접미사를 빼고 구·동 우선으로 가져가야 "{가게명} 영등포구 영등포동" 형태가 돼서 매칭률이 높아짐.

- **왜 차단 감지를 두는가**: 본문 fetch가 일시적으로 0% 성공으로 떨어지면 IP 차단/UA 차단 가능성. 그 잡을 `failed`로 마킹해두면 30일 freshness에 안 걸려서 다음 잡에서 재시도 가능 (`_should_refresh`는 freshness 판정에 status 무관하게 collected_at 기준이라 — 이 점은 향후 개선 여지: status='done' 행만 freshness 판정에 쓰는 게 더 정확).

- **왜 cache-hit 분기는 트리거 안 하는가**: 라우터 cache-hit 분기는 `instagram_post_cache`(shortcode 기반) hit이라 동일 URL 재공유 케이스만 해당. 그 시점이면 Place는 이미 있고 누군가 enrichment를 트리거한 상태. 30일 stale인 Place가 다른 인스타 URL로 들어오는 경우(=cache miss → 워커)에는 `_should_refresh`가 결국 발동하니 실무상 영구 stale 가능성 없음. 의도된 deferral.

- **법적 고려**: 캡스톤 학술 프로젝트, 비공개·비상업 사용, 사용자 노출 없음. 상용 배포 시 약관·robots.txt 재검토 필요.

- **기존 `naver_blog.py`(`/places/from-naver` 백그라운드 태스크용)와 공존**: 새 모듈은 별도 이름(`naver_blog_search.py`)으로 추가했고, `/share`·`/save` 진입점에서만 새 RQ 잡 흐름을 탄다. 기존 `/places/from-naver` BackgroundTasks 흐름은 그대로 유지. 두 흐름 모두 `place_raw_data(provider='naver_blog')` 같은 테이블에 쓰지만 30일 갱신 로직이 양쪽 데이터를 일관되게 다룬다(어느 경로로 들어왔든 30일 안이면 fresh로 판정). 향후 통합 정리할 가치 있음 — 별도 작업으로 분리.

## 배운 점

- `place_raw_data` / `place_reviews`가 이미 멀티 프로바이더 가정으로 설계돼 있어 새 테이블 없이 깔끔하게 끼울 수 있었음. 초기 스키마 설계의 가치.
- 쿼터 카운트는 "잡 done" 자체가 아니라 "실제 외부 호출 발생 여부"를 봐야 함. 스킵 잡까지 카운트하면 한도가 잘못 닫힘. 다른 가드 로직에도 동일 원칙 적용 필요.
- partial unique index에 `ON CONFLICT DO NOTHING`을 쓰려면 `index_where`를 명시해야 conflict target 추론이 성공한다. SQLAlchemy `pg_insert(...).on_conflict_do_nothing(index_elements=..., index_where=...)` 형태.
- 한국 주소의 첫 토큰("서울특별시")은 검색어로는 노이즈에 가까움. 행정구역 접미사 기반 토큰 필터링이 작은 기교지만 매칭률에 의미 있는 차이를 만든다.
- 워커 안에서 RQ enqueue가 필요한 경우, FastAPI의 `request.app.state` 큐 핸들이 안 보인다. 별도의 `queue.get_default_queue()` 헬퍼로 환경변수 기반 큐 인스턴스를 새로 만드는 게 깔끔.
