# 인스타 캐러셀 전체 이미지 저장

## 배경 / WHY

`/instagram/share`와 `/instagram/save` 양쪽 모두, 게시물의 **썸네일(첫 이미지) 1장만** PlaceImage로 저장하고 있었다. AI팀이 이 단일 이미지로 공간 DNA(MBTI 4축)를 분석하면 다음 두 가지 한계가 발생한다.

1. **단일 컷 편향**: 인테리어·메뉴·외관 등 분위기 신호가 분산된 캐러셀에서 첫 컷 하나만 보면 결과가 좌우된다.
2. **자막+음식 사진 노이즈**: 큐레이션 게시물·맛집 후기 게시물의 첫 컷은 대개 큰 자막이 박힌 음식 사진이라, 공간 분위기 분석에 부적합한 입력이 들어간다.

이번 작업은 캐러셀 슬라이드를 **모두 영구 저장**해 이후 분석기가 다중 입력을 활용할 수 있도록 데이터 적재 단계를 정리한다. 분석기 자체(다중 이미지 활용)는 후속 브랜치로 분리.

## 핵심 변경

### `app/services/spot_creator.py`
- `InstagramData`에 `image_urls: Optional[list[str]] = None` 추가.
- `create_spot_from_naver`의 단일 이미지 블록을 리스트 루프로 교체:
  - `image_urls` 우선, 없으면 `thumbnail_url`을 1장짜리 리스트로 폴백
  - 호출 내 같은 URL은 `dict.fromkeys`로 dedupe
  - 각 URL을 Supabase Storage에 업로드 시도(실패 시 원본 URL로 폴백 — 기존 가용성 우선)
  - 첫 장만 `is_representative=True`, 나머지는 `False`
  - `Spot.thumbnail_url`은 첫 장의 영구 URL — 기존 동작 동일

### `app/services/instagram_share.py`
- `_to_instagram_data`가 `image_urls=list(crawl.images)`를 함께 전달. `crawl.images`는 이미 Apify 액터가 슬라이드 전체를 평탄 리스트로 채워 보낸다.

### `app/schemas/instagram.py` + `app/routers/instagram.py`
- `InstagramSaveRequest.image_urls: list[str] | None` 추가.
- 라우터에서 `body.image_urls or [body.thumbnail_url]`로 합쳐 `InstagramData`에 전달.
- 기존 `thumbnail_url`만 보내는 클라이언트는 자동 폴백 — backward compatible.

### `scripts/_oneoff_check_carousel_extraction.py` (신규)
- 진단 도구. 운영/로컬 DB의 `place_raw_data(provider='instagram')`에서 캐러셀 raw 응답 키를 직접 들여다본다.
- `images` 평탄 배열 길이, `displayUrl`·`childPosts`·`sidecarChildren`·`carouselMedia` 키 존재 여부, `_normalize_apify` 결과 길이까지 같이 출력.
- 가설 검증용: Apify 액터가 캐러셀을 어떤 키로 반환하는가 (핸드오프 문서상 평탄 `images` 패턴으로 추정 → 실제 raw로 최종 확인).

## 결정 사항 (3중 검증)

### 결정 1 — 본 브랜치는 추출+저장까지만
분석기 다중 이미지 활용은 별도 브랜치로 분리. PR을 작게 유지하고 영향 격리(분석기는 외부 API 호출이 있어 본 작업과 결합하면 회귀 위험↑). 또 분석기 변경엔 AI 프롬프트·DB 스키마(분석 결과 저장 형태) 검토가 따로 필요.

### 결정 2 — 동기 업로드
캐러셀 최대 10장 × 평균 1초 ≈ 10초. `/share`는 RQ 워커 내부 실행이라 사용자 응답 시간 무관. `/save`는 사용자가 폼 제출 후 대기하는 흐름이라 견딜만함. 백그라운드 잡으로 빼면 중복 enqueue·실패 재시도 복잡도가 늘어나는데 ROI 낮음 — 성능 이슈가 실측되면 별도 처리.

### 결정 3 — SpotResponse 노출은 범위 밖
이번 작업의 목적은 AI 분석 입력 확보 → DB 적재. 클라이언트 UI에 다중 이미지 노출은 별도 디자인·페이지네이션·캐러셀 컴포넌트 설계가 필요해 분리.

### 결정 4 — PlaceImage 중복 가드 미추가
기존 코드도 같은 image_url을 다른 storage가 저장할 때 중복 PlaceImage 행을 만든다. 본 작업이 신규 회귀를 만들지 않으므로 일관성 유지. 정리는 별도 작업으로 미룬다.

### 결정 5 — `_normalize_apify` 자식 키 폴백은 진단 후 결정
핸드오프 문서 3곳(`notes/2026-05-09-ai-team-handoff.md:170`, `notes/2026-05-14-space-dna-auto-trigger.md:140`, `seeds/README.md:125`)이 모두 `raw_payload['images']` 평탄 배열 패턴을 가리킴 → 현재 `list(raw.get("images") or [])`로 이미 캐러셀 전체가 잡힐 가능성 높음. `childPosts`/`sidecarChildren` 폴백은 "가상의 안전망"으로 격하해, 진단 스크립트로 실제 raw를 본 뒤 누락이 발견된 경우에만 추가.

## 알려진 한계 (후속 작업)

- **분석기는 여전히 1장만 본다**: `space_dna_analyzer._pick_image_url`이 `is_representative.desc()` + LIMIT 1 패턴. PlaceImage 다중 행을 만들어도 분석 입력은 첫 장만. 본 작업의 가치를 실제로 끌어내려면 분석기와 AI 프롬프트가 다중 이미지를 받도록 변경 필요.
- **운영 DB 기존 25건 백필 없음**: 본 작업 이전 저장된 Place들은 PlaceImage가 1장만 있는 상태로 남는다. `notes/2026-05-14-space-dna-auto-trigger.md:140` 백로그("raw_payload['images']에서 다시 끌어와 PlaceImage로")와 동일 작업으로 처리.

## 검증

- [ ] 진단 스크립트로 운영 raw 1~3건 키 확인 → 평탄 `images`가 전체를 잡는지 검증
- [ ] `poetry run python -c "import app.main"` import 스모크
- [ ] 실서비스 캐러셀 1건 end-to-end 후 DB 확인: `place_images.place_id=<id>`로 다중 행 + 첫 행만 `is_representative=True`
- [ ] 단일 이미지 게시물 회귀 — 기존처럼 1행 적재
