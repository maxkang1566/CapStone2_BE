# 2026-05-14 — LLM 기반 인스타 캡션 장소 추출기 도입

## 작업 내용

`POST /instagram/share`의 캡션 → 장소 후보 추출 단계에 LLM(claude-haiku-4-5) 추출기 도입. 정규식(`place_extractor.py`)이 큐레이션 게시물에서 가게명을 못 잡는 한계를 보완.

### 변경

- 신규 `app/services/place_extractor_llm.py` — `extract_places(caption, *, hashtags) -> Optional[list[str]]`
- `app/services/instagram_share.py`
  - `_NON_PLACE_CATEGORY_GROUPS`에 `"교통,운수"` 추가 (지하철역 차단)
  - `share_post`에 LLM 우선 호출 + 정규식 폴백 (line 152-164)
  - LLM이 unique≥2건 추출하면 disambiguator skip하고 needs_selection 직행 (line 199-218)
- `scripts/_oneoff_check_instagram_share_extraction.py` — `--with-llm` 플래그 추가

## 진단 (DWtRCqpkZPt 게시물)

성북구 카페 5곳 큐레이션 게시물에서 정규식이 가게명 0건 추출.

캡션 구조:
```
 ❶ 티히커피 @teehee.coffee
🚏월곡역
📍서울 성북구 오패산로 31 1층
```

7개 정규식 추출 경로가 모두 실패:
- `_PIN_BEFORE_RE`: `📍` 앞 줄을 잡는데 그 줄이 `🚏역명` → 가게명 누락
- 나머지 6개: 라벨 없음 / 첫줄 카피만 / 해시태그가 `*카페` generic 차단

결과: 사용자 화면에 노이즈 4건 표시 — 고려대역 6호선(교통,운수), 보문사(불교), 스타벅스 한성대입구역점(카페,디저트), 치코커피(카페,디저트). 실제 가게 5곳은 0건 매핑.

## 결정 이유 (WHY)

### 응답 스키마를 `{queries: list[str]}` 1필드로 극단 단순화

처음 안: `[{name, address, kind, ...}]` + 메타 필드(`is_place_post`, `reason`). 단순성 검증 결과 모두 제거.

이유:
- `kind` enum 분류는 카테고리 차단(`_NON_PLACE_CATEGORY_GROUPS`)과 동일 방어 → 중복
- `is_place_post`는 결정 분기에 안 쓰는 메타 → 토큰만 소비
- `reason` 디버깅용은 로그로 충분
- `name`/`address` 분리 후 `_build_query`로 결합 → LLM이 직접 query 작성하면 후처리 0

환각·잡음 방어는 3중 막아둠:
1. `_NON_PLACE_CATEGORY_GROUPS` 카테고리 차단
2. 네이버 검색 0건이면 후보 자동 폐기
3. `unique == 1`일 때만 자동 저장

### env flag 추가 안 함

처음 안: `PLACE_EXTRACTOR_LLM_ENABLED` 환경변수로 ON/OFF.

이유: `ANTHROPIC_API_KEY` 미설정 시 이미 None 반환 → 비활성. 추가 flag는 disable 경로 둘 → 운영 의사결정 분산. 비상 차단은 API 키 제거로 통일.

### 큐레이션 disambiguator skip 분기

LLM이 5개 정확 추출 → unique 5건 → disambiguator는 "정답 1개" 모델이라 큐레이션에서 임의 1개를 골라 자동 저장하는 회귀 위험.

대응: `used_llm=True AND unique≥2`이면 disambiguator skip하고 needs_selection 직행. 정규식 폴백 경로(`used_llm=False`)는 한 가게의 다른 표현이 dedupe 안 된 경우가 많아 disambiguator의 1개 선택이 여전히 유효 → 기존 흐름 유지.

### "교통,운수" 카테고리 추가의 단독 효과

가드 단독으로는 진단 게시물 노이즈 4개 중 `고려대역 6호선` 1건만 차단. `보문사`(불교)·`스타벅스`(카페,디저트)·`치코커피`(카페,디저트)는 음식점/일반 카테고리라 차단 안 됨.

본질 해법은 LLM 추출이고 이 가드는 LLM 환각·분류 오류 시 안전망.

## 배운 점

- **정규식 휴리스틱의 부서지기 쉬움**: `_PIN_BEFORE_RE`가 `📍` 앞 줄을 가게명으로 가정. 캡션 포맷이 `가게명/역/주소` 3줄로 분리되면 가정 무너짐. 정규식은 한 가지 캡션 포맷에 최적화되면 다른 포맷에서 0건 추출.
- **LLM disambiguator의 정책 한계**: "정답 1개" 모델은 모호한 단일 장소 게시물엔 효과적이지만, 큐레이션 게시물엔 부적합. 추출 단계의 신뢰도(LLM이냐 정규식이냐)로 분기 정책을 다르게 가져가야 회귀 안 남.
- **응답 스키마 단순화의 비용**: `kind`/`address` 분리 안 하면 LLM이 부정확한 query를 만들 수 있지만, 후처리 0 + 3중 방어가 받쳐주면 단순성이 더 큰 이득. 향후 환각률 측정 후 필요하면 추가.

## 검증 결과

- import 스모크 PASS (`place_extractor_llm`, `instagram_share`)
- 정규식 폴백 동작 확인: 로컬 `.env`에 `ANTHROPIC_API_KEY` 없는 상태에서 dry-run 실행 → "ANTHROPIC_API_KEY 미설정 — LLM 추출기 비활성" 로그 + None 반환 + 정규식 폴백 메시지 정확 출력
- LLM 실제 호출 검증은 사용자 환경(운영 또는 키 설정된 로컬)에서 dry-run으로 수행 필요

## 사용자 액션 필요

1. `.env`에 `ANTHROPIC_API_KEY` 추가 (이미 운영에는 있음 — disambiguator 작동 전제)
2. dry-run 실행:
   ```bash
   poetry run python scripts/_oneoff_check_instagram_share_extraction.py \
       --url https://www.instagram.com/p/DWtRCqpkZPt/ --with-llm --with-naver
   ```
3. 성공 조건: LLM 결과에 `티히커피`·`마가밀`·`카페야벳`·`아일`·`카페 기쁜소식` 5건이 행정구역 결합 형태로 등장. 네이버에서 음식점/카페 카테고리로 매핑.
4. 회귀 확인: 단일 장소 게시물 URL 1개로도 dry-run → LLM이 1개만 추출하는지.

## 미해결 / 후속 작업

- 다중 장소 자동 저장 정책 — 현재 url 1개당 spot 1개 전제(`_existing_spot_for_url`). 큐레이션 5개 중 1개 저장 후 나머지 4개 접근 불가 잠금 효과. N spot 동시 저장 정책 + 단축 경로 재설계 필요.
- LLM 호출 캐싱 — 같은 URL 반복 share 시 LLM 매번 호출(현재 캐시는 `fetch_post`만). 같은 캡션이면 LLM skip하도록 결과 캐싱.
- 비용 가드 본격화 — `ANTHROPIC_API_KEY` 단일 토글 외에 월 호출 한도(`_is_apify_budget_exceeded` 패턴) 추가.
- 캡션 chunking — 2000자 초과 큐레이션 게시물의 후반부 누락.
- LLM 응답 평가 자동화 — `seeds/golden_set.csv`에 캡션→기대 가게명 라벨 추가해 회귀 detection.
- LLM 빈 응답 회귀율 측정 — 운영 첫 2주 빈 응답 로그 수집. 회귀 발견 시 `not candidate_texts: fallback to regex` 정책 변경.

## 관련 파일

- `app/services/place_extractor_llm.py` (신규)
- `app/services/place_disambiguator.py` (참고 패턴)
- `app/services/place_extractor.py` (정규식 폴백)
- `app/services/instagram_share.py` (통합 지점)
- `scripts/_oneoff_check_instagram_share_extraction.py` (검증 도구)
