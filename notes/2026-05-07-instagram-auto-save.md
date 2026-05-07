# 2026-05-07 — 인스타그램 자동 장소 매핑 + 저장 (방식 D 도입)

## 작업 내용

`POST /instagram/share` 신규 엔드포인트 도입. 사용자가 인스타 게시물을 공유하면 서버가 캡션에서 장소 후보를 추출하고 네이버 Local Search로 검색해, **유일한 후보로 수렴할 때만 자동으로 Spot을 생성**한다. 후보가 0개이거나 여러 개일 때는 후보 리스트와 크롤 데이터를 클라이언트로 돌려보내, 사용자가 직접 고르고 기존 `/instagram/save`로 저장하는 폴백 흐름으로 빠진다.

추가/수정 파일:
- 신규: `app/services/naver_local_search.py` (네이버 Local Search API 래퍼)
- 신규: `app/services/place_extractor.py` (캡션 → 후보 텍스트 추출)
- 신규: `app/services/spot_creator.py` (Place upsert + Spot 생성 로직 추출, `/save`/`/share` 공통 사용)
- 신규: `app/services/instagram_share.py` (오케스트레이션)
- 수정: `app/services/instagram_pipeline.py` (`_normalize_apify`에서 restricted 응답을 description→caption 매핑하도록 보강)
- 수정: `app/routers/instagram.py` (`/instagram/share` 핸들러 추가, 기존 `save_instagram_spot`은 spot_creator에 위임)
- 수정: `app/schemas/instagram.py` (Share 요청·응답 스키마 추가)

## 결정 이유 (WHY)

### 왜 자동 저장 흐름을 도입하는가
기존 방식 C(사용자가 네이버 지도에서 직접 장소 선택)는 *비로그인 OG 메타로는 본문/주소를 거의 못 가져온다*는 전제에서 만들어졌다. 그런데 Apify를 도입하면서 인스타가 차단해 `restricted_page` 응답을 줘도 **`description` 필드에 본문 전체 + 임베디드 주소(`📍서울시 영등포구 …`)** 가 안정적으로 들어온다는 점이 검증됐다(shortcode `DBYq3L_yK1o`로 확인). 즉 방식 C의 전제가 무너졌고, 자동 매핑이 비로소 시도해볼 만한 단계가 됐다.

### 왜 "유일한 후보로 수렴할 때만" 자동 저장하는가
오탐(잘못된 Spot이 박히는 상황)이 사용자 경험에서 가장 치명적이다. 캡션에 여러 장소가 언급되거나 추출 후보들이 서로 다른 네이버 장소로 분기되면 자동 결정을 포기하고 사용자에게 선택권을 넘긴다. 정확도 우선 정책.

### 왜 다중 장소 게시물도 수동 폴백인가
사용자 피드에서 "단일 리뷰"와 "큐레이션(예: 강남 카페 5선)" 비율이 비슷해, 자동 분기 로직(휴리스틱)으로는 어느 한쪽을 무조건 유리하게 선택하면 다른 쪽에서 손해를 본다. LLM 도입 없이 두 패턴을 안정적으로 구분할 방법이 없어 자동을 포기하고 후보 리스트로 위임한다.

### 왜 LLM 추출은 안 쓰는가
MVP 단계에서 외부 API 비용·지연·의존을 추가하기 부담스럽다. 정규식+휴리스틱 추출이 부정확해도 "유일 후보 수렴" 가드가 오탐을 막아주므로 보수적 흐름과 잘 맞물린다. 향후 정확도 개선이 필요해지면 그 시점에 도입.

### 왜 신규 엔드포인트인가 (기존 /save 확장이 아니라)
- `/save`는 클라이언트가 이미 고른 결과를 받는 단순 저장 책임
- `/share`는 자동 매핑 시도 + 분기 응답 책임
서로 책임이 달라 한 엔드포인트에 묶으면 응답 분기·필드 분기가 복잡해진다. `/save`는 수동 폴백 경로에서 그대로 재사용된다.

## 배운 점

- **인스타 비로그인 차단은 Apify 일반 스크레이퍼에서도 그대로 발생**: `apify/instagram-scraper` 액터가 `restricted_page` 에러를 자주 돌려준다. 본문/이미지 정도는 OG와 비슷한 수준으로만 받을 수 있고, 위치 좌표·해시태그 배열 같은 풍부 데이터는 로그인 처리 액터가 필요하다. 후순위 과제로 분류.
- **Apify 액터 ID 선택은 input 스키마와 직결**: `apify/instagram-post-scraper`는 `username`을 받고, `apify/instagram-scraper`는 `directUrls`를 받는다. 처음에 잘못된 액터로 가면 `Field input.username is required` 같은 메시지로 즉시 실패한다.
- **`except SomeError: pass`는 디버깅의 적**: silent swallow 때문에 실제 원인을 두 번 우회해서 확인했다. 모든 polite-fallback 분기에는 `logger.warning`이 필수.
- **Naver Local Search의 mapx/mapy는 WGS84 × 10⁷ 정수**(2018-12 이후): 별도의 KATECH 변환 없이 `value / 1e7` 로 끝난다. 옛 자료에 KATECH 변환식이 남아있어 헷갈릴 수 있음.
- **Naver Local Search는 `naver_place_id`를 직접 주지 않는다**: `link` 필드는 가게의 외부 사이트 URL(catchtable, 자체 홈페이지 등)이라 식별자로 못 쓴다. 같은 가게에 일관된 ID를 부여하려면 `name + roadAddress`를 정규화한 SHA-1 해시를 자체 ID로 사용한다.
- **추출 우선순위는 가게명 > 주소**: 네이버 Local Search는 가게명 매칭이 강하고 주소만으로는 거의 0건 반환한다. 큐레이션 게시물(`➊삼원가든📍서울 강남구...`)에서 📍 뒤(주소)만 잡으면 검색 결과 0건이 되니, **📍 앞 가게명도 같이 추출**하는 패턴이 핵심.
- **해시태그는 generic 접미어 필터가 필수**: `*맛집`/`*카페`/`*데이트` 같은 주제 태그를 그대로 검색하면 무관한 가게가 무더기로 매칭돼 unique count를 부풀린다. 결과적으로 자동 저장이 거의 안 일어남. 접미어 denylist로 걸러야 specific한 상호명 태그(`#오얏키친` 등)만 살아남는다.
- **uvicorn `reload=True` + Playwright는 Windows에서 reload 시 행 걸리는 알려진 이슈**: 코드 변경 시 reload watcher가 child process를 죽이려 하지만 Playwright subprocess가 안 죽어서 listening 소켓이 좀비 상태로 남는다. 부득이하게 PC 재시작 또는 다른 포트 사용으로 회피.

## 후속 (2026-05-07): "장소 게시물 아님" 분기 분리

응답 상태에 `not_a_place_post`를 추가했다. 이전에는 `len(unique) == 0`인 경우도 `needs_selection`로 응답하면서 `candidates`만 빈 배열로 보냈는데, 이로 인한 UX 모호함을 제거.

**왜 분리했나**: 빈 후보 리스트로 needs_selection을 보내면 클라이언트가 (1) `candidates.length === 0`로 별도 분기를 추가해야 하고, (2) "후보 선택 화면이 빈 채로 뜨는" 어색한 상태를 방어해야 한다. 상태 enum으로 명시 분기하면 모바일 클라이언트가 `saved` → 토스트, `needs_selection` → 후보 모달, `not_a_place_post` → "장소 게시물이 아닙니다" alert 식으로 자연스럽게 매핑 가능.

**트리거 조건**: 추출 후보 자체가 0개일 때만이 아니라, **네이버 Local Search 매칭까지 끝낸 뒤 유니크 `naver_place_id`가 0개**일 때(추출은 됐어도 다 노이즈여서 매칭 0건인 경우 포함). 사용자가 명확히 결정한 사항.

**`needs_selection`의 의미가 좁아짐**: 이제 "유니크 후보 2개 이상에서 사용자 선택"만 의미한다. 클라이언트가 기존에 빈 후보로 분기하던 코드가 있었다면 깨질 수 있음. 모바일 앱은 status enum 기반으로 case 분기 권장.

**`crawl_data`는 not_a_place_post에서도 채움**: 클라이언트가 "장소 게시물이 아닙니다"라는 알림과 함께 캡션·썸네일 미리보기를 보여주면 사용자가 "아 그렇구나" 하고 닫기 편하다. 디버깅에도 유리.

**저장 동작은 0건**: not_a_place_post 응답 시 spots/places/place_raw_data에는 어떤 행도 추가되지 않는다. instagram_post_cache는 fetch_post 호출에 따라 채워질 수 있음(별개 캐시 레이어).
