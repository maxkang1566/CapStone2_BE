# 2026-05-22 — 다중 장소 게시물: 선택된 장소의 이미지만 분류 저장

## WHY

인스타 큐레이션 게시물(예: "강남 5대 카페")은 한 게시물에 여러 장소 + 장소별 여러 사진이 섞여 있다. 기존 흐름:

- `/instagram/share`에서 후보 2개 이상이면 `status="needs_selection"` → 사용자가 1개 장소 선택 → `/instagram/save` 호출
- 그러나 `/save`는 캐러셀 전체 이미지를 그 1개 Spot에 그대로 적재 — **다른 장소 사진까지 같은 갤러리에 섞여 들어감**
- 결과: 사용자가 본 갤러리에 무관한 사진이 보이고, 향후 AI 공간 DNA 분석이 다른 장소 사진까지 입력으로 받아 정확도 저하

이 PR은 `/save` 시점에 **Claude Vision으로 캐러셀 이미지를 후보 장소 중 하나에 배정**하고, **사용자가 선택한 장소(naver_place_id)에 배정된 이미지만 저장**한다.

## 결정

### 1. needs_selection 흐름은 그대로

`share_post`는 후보가 2개 이상일 때 여전히 `needs_selection`을 반환한다. 자동 다중 저장은 도입하지 않았다 — 사용자 의도 확인이 우선이고, 자동 다중 저장은 별도 UX 결정이 필요한 범위이기 때문.

### 2. 분류는 `/save` 시점 지연 호출

`/share`에서 미리 분류하지 않는다 — 사용자가 저장하지 않는 게시물(다른 후보를 고르거나 아예 취소)의 비용 낭비를 피하기 위해. 동기 호출이라 `/save` 응답 시간이 분류 호출 시간만큼(~2~5초) 증가한다.

### 3. 분류용은 다운스케일, DB 저장용은 원본

비용 최적화 핵심:
- 분류기 모듈이 자체적으로 이미지를 다운로드해 **PIL로 512px max dim 리사이즈 → JPEG 재인코딩 → base64**로 Claude에 전달
- DB(Supabase Storage)로 가는 사진은 `image_storage` 기존 경로가 별도로 원본을 받아 그대로 업로드. 분류 모듈은 이 흐름을 건드리지 않는다
- 결과: 한 URL을 두 번 다운로드(분류용 + 저장용)하지만 데이터량이 작아 무시 가능

비용 추산: 게시물당 약 $0.0075 (10이미지 × 350토큰(512px) + 캡션·후보 ~1.5k 토큰, Haiku 4.5 단가)

### 4. 단일 자동 저장 케이스는 손대지 않음

후보가 1개라서 `share_post`가 곧장 자동 저장한 경우(PR #7 동작), 비교 대상이 없어 분류 자체가 의미 없다. matcher 호출 없이 캐러셀 전체를 그대로 적재. 회귀 없음.

### 5. 폴백 정책 — 첫 후보에 전체 몰아넣기

분류기 어디서든 실패하면(API 키 미설정, 타임아웃, 응답 검증 실패, 이미지 전건 다운로드 실패) `{0: list(range(len(image_urls)))}` 반환. 그러면 `/save`의 선택 장소가 후보 0번이면 전체를 받고, 후보 N번이면 0장이 되니까 → **0장 폴백 추가 로직(`image_urls[0]` 강제)** 이 호출부에 있어 표시 없는 Spot은 발생하지 않는다.

### 6. 클라이언트가 분류 컨텍스트 전달

`/save` 요청에 `candidates_context: list[PlaceCandidate]` 신규 옵셔널 필드. needs_selection 응답의 `candidates`를 그대로 되돌려보내면 됨. 미제공이면 분류 미수행 — 기존 클라이언트 호환.

추가로 `InstagramCrawlData.image_urls` 신규 필드를 needs_selection 응답에 노출 — 캐러셀 전체 URL이 클라이언트에 도달해야 `/save`로 되돌릴 수 있음(검증 단계에서 발견한 빈틈 해소).

### 7. 모델·SDK 패턴 통일

`claude-haiku-4-5` + `client.messages.parse(output_format=Pydantic)` — 기존 `place_extractor_llm`·`place_disambiguator`와 동일. 이번이 코드베이스에서 vision input 첫 사용 사례.

## 알려진 한계

- **호출 간 비결정성**: 2026-05-22 로컬 e2e 검증에서 같은 게시물·이미지·후보 조합으로 두 번 `/save`를 호출했을 때, Claude가 슬라이드 1을 1차에선 place 0(티히 커피), 2차에선 place 1(마가밀)로 배정 → 같은 이미지가 두 Place의 PlaceImage에 동시 존재. 본 PR에 `temperature=0.0` 명시로 변동 폭은 줄였으나 Vision 입력은 완전 결정성 보장 안 됨. 완전 결정성을 원하면 후속 작업으로 **Redis에 `{shortcode + candidates_hash → 매핑}` 캐싱** — 같은 게시물의 N번째 저장이 같은 매핑 재사용 + Claude 호출 0회로 비용 절감 동시. 캐시 키 설계·TTL(Instagram CDN URL 만료가 4~5일이라 그보다 짧게)·Redis 다운 폴백 등 코드량이 있어 본 PR엔 미포함.
- **클라이언트 합류 필요**: 프론트가 `candidates_context`와 `image_urls`를 같이 보내도록 변경되기 전까지 본 분류 동작은 작동하지 않음. 안전 폴백(미제공 시 기존 동작 유지)으로 회귀는 없음.
- **분류 정확도 정량 평가 없음**: 라벨된 평가 셋이 없어 정성 평가만 가능. `scripts/_oneoff_test_image_matcher.py`로 케이스별 확인.
- **prompt caching 미사용**: 분류 지침이 매 호출 반복 — 호출 빈도가 늘면 도입해서 절반 절감 가능.
- **다중 동시 저장 UX 미지원**: 현재는 한 번에 1개 장소만 저장. "5개 다 저장" UX는 별도 디자인 필요. 분류 모듈은 멀티클래스 결과를 내므로 재사용 가능.
- **다운로드 중복**: 같은 URL을 분류 모듈·image_storage가 각각 다운로드. 합치려면 download 헬퍼 도입 + image_storage 시그니처 변경 필요 — 후순위.
- **PlaceImage 중복 가드 없음**: 같은 image_url이 같은 place에 두 번 들어갈 가능성(PR #7 메모에도 기록). 본 PR이 신규 회귀를 만들지 않음.

## 변경 파일

- (신규) `app/services/image_place_matcher.py` — Claude Vision 분류 모듈
- `app/schemas/instagram.py` — `InstagramSaveRequest.candidates_context`, `InstagramCrawlData.image_urls` 추가
- `app/services/instagram_share.py` — `share_result_to_response`에서 image_urls 채우기
- `app/routers/instagram.py` — `/save`에서 matcher 호출
- (신규) `scripts/_oneoff_test_image_matcher.py` — 진단 도구
- `pyproject.toml` — pillow 추가
