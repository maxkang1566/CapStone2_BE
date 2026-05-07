# 네이버 블로그 리뷰 수집 (2026-05-05)

## 작업 내용
- `app/services/naver_blog.py` 신규: 네이버 블로그 검색 API 연동 + 백그라운드 리뷰 수집
  - `search_blog_posts()` async (재사용 가능한 일반 함수)
  - `_search_blog_posts_sync()` 동기 — 백그라운드 태스크 전용
  - `collect_reviews_for_place(place_id, query, raw_data_id)` 백그라운드 진입점
  - HTML 태그/엔티티 제거, postdate(YYYYMMDD) 파싱
- `app/routers/places.py`:
  - `POST /places/from-naver`: created=True일 때만 BackgroundTasks로 리뷰 수집 트리거
  - `GET /places/{id}/reviews` 신규 (페이징)
- `.env.example`: `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 추가

## 결정 이유 (WHY)
- **본문 크롤링 추가 (수정됨)**: 처음엔 API 응답만 저장하려 했으나, 네이버 블로그 검색 API의 `description`은 검색 결과용 스니펫(약 150자)일 뿐 실제 후기 본문이 아니어서 AI 분석에 부족함을 확인. 본문 크롤링을 추가.
  - 모바일 URL(`m.blog.naver.com`) 변환: 데스크톱은 frameset 구조라 본문이 iframe 안에 있어 파싱 어려움. 모바일은 본문이 HTML에 직접 렌더링됨.
  - 셀렉터 fallback 체인: 네이버 Smart Editor 3 → 구버전 → 일반 블로그 플랫폼 → og:description.
  - 본문 추출 실패 시 스니펫 저장: AI팀이 길이로 필터링 가능하도록 무엇이든 저장.
  - 본문 최대 10000자 제한: DB 부하 및 추후 AI 토큰 비용 방어.
  - User-Agent 위장: 일부 블로그가 봇 차단을 위해 빈 페이지 반환하는 경우 회피.
- **백그라운드 태스크**: 장소 저장 응답 속도(UX) > 리뷰 수집 즉시성. 실패해도 장소 저장은 영향 없음.
- **별도 DB 세션 (SessionLocal 직접 사용)**: 라우터 응답 시점에 request scope 세션이 닫히기 때문. BackgroundTasks는 응답 후 실행.
- **동기 httpx 사용 (백그라운드용)**: `BackgroundTasks.add_task`에 등록한 함수는 sync도 thread pool에서 실행됨. async로 만들면 별도 이벤트 루프 처리 필요해 복잡도 증가.
- **created=True에서만 트리거**: 기존 장소 재조회 시 중복 호출 방지. 어차피 collect_ 함수가 1회 수집 정책으로 막지만, API 호출 자체를 안 하는 게 효율적.
- **1회 수집 정책**: place_id + provider="naver_blog" 존재 여부로 early return.
- **외부 ID = 블로그 link**: PlaceReview unique index `(place_id, provider, external_review_id)` 활용. 동일 블로그 글 중복 저장 자동 방지.
- **NAVER_CLIENT_ID 미설정 시 빈 리스트 반환**: 백그라운드에서 예외로 트랜잭션 망가뜨리지 않음. 장소 저장은 항상 성공.

## 배운 점
- FastAPI BackgroundTasks는 응답 후 실행되므로 request scope DB 세션을 절대 사용하면 안 됨 → SessionLocal로 새 세션을 떠서 사용해야 함.
- 네이버 블로그 API description은 HTML(`<b>` 강조 태그) 포함이라 strip 필수.
- postdate는 YYYYMMDD 8자리 문자열 — 8자리 검증 후 파싱하는 게 안전.

## 후속 작업
- `.env`에 실제 NAVER_CLIENT_ID/SECRET 입력 (사용자 작업)
- AI팀에 PlaceReview 스키마와 GET /places/{id}/reviews 공유
- 다음 백로그: M-3 공간 DNA 조회 API
