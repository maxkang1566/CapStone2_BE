# 2026-06-01 — place 다중 이미지 GET 엔드포인트 추가

## 작업 내용

장소(Place)에 저장된 이미지 전체를 반환하는 신규 엔드포인트 추가.

- `app/schemas/place.py` — `PlaceImageResponse` 스키마 신설 (`id, place_id, image_url, source, is_representative, created_at`)
- `app/routers/places.py` — `GET /places/{place_id}/images` 핸들러 추가. `PlaceImage`를 `is_representative DESC, created_at ASC`로 정렬해 `list[PlaceImageResponse]` 반환. place 미존재 시 404.

## 문제 (WHY)

저장 경로는 이미 **다중 이미지**를 지원한다:
- IG 캐러셀 → `spot_creator.py:364-377`에서 `place_images`에 N행 INSERT, 첫 장만 `is_representative=True`
- 다중 장소 분류, 네이버 직접 저장도 동일 테이블에 적재

그런데 **읽기 경로가 전부 단일/없음**이었다 (4개 surface 전수 감사 + 적대적 검증으로 확인, refuted=false):

| 엔드포인트 | 스키마 | 이미지 |
|---|---|---|
| `GET /places/{id}`, `GET /places` | `PlaceResponse` | **0장** (이미지 필드 자체 없음) |
| `GET /storages/{id}/spots`, `.../spots/{id}` | `SpotResponse` | 1장 (`thumbnail_url`) |
| `GET /me/pins` | `PinResponse` | 1장 (`thumbnail_url`) |
| `POST /instagram/save`·`/share`, `GET /share-jobs/{id}` | `→SpotResponse` | 1장 |

핵심 원인 2가지:
1. `Spot.thumbnail_url`은 `PlaceImage`를 **참조하는 게 아니라** 저장 시점에 대표 1장 URL을 Spot 행에 복사해 둔 별도 단일 컬럼(`models.py:203`). N장을 저장해도 다시 꺼낼 경로가 없었음.
2. 다중 `PlaceImage`를 list로 읽는 곳은 내부 Space DNA 분석기(`space_dna_analyzer.py:_pick_image_urls`, AI API 전송용)뿐 — 클라이언트로 직렬화 안 됨. 즉 `Place.images` 관계가 **write-only** 상태였다.

→ 사용자가 캐러셀 N장을 저장해도 앱에서 그 N장을 갤러리로 다시 볼 수 없었다.

## 결정 이유 (WHY this approach)

3가지 옵션 중 **전용 엔드포인트**(`GET /places/{place_id}/images`)를 택함:
- **(A) 전용 엔드포인트 ← 채택**: 기존 스키마 무수정. `pin.py` 설계 의도(슬림 마커 + 상세는 lazy 로딩)와 일관 — 마커 클릭 → 갤러리 lazy fetch. 검색·리스트 응답 페이로드에 영향 없음.
- (B) `PlaceResponse.images` 추가: `GET /places/{id}` 한 방에 다 주지만, `GET /places` 검색 리스트에도 따라붙어 N+1/페이로드 부담.
- (C) `SpotResponse.place_images` 추가: `list_spots`에도 따라붙어 스팟마다 장소 이미지 중복 전송.

정렬을 `is_representative DESC, created_at ASC`로 맞춘 이유: Space DNA 분석기 `_pick_image_urls`와 **동일 정렬**이라 대표 이미지가 항상 첫 번째로 와서 프론트가 별도 정렬 없이 [0]을 대표로 쓸 수 있음.

권한: 다른 `places.py` 조회 엔드포인트(`get_place`, `get_place_reviews` 등)와 동일하게 `get_current_user`만 요구 — Place는 storage에 묶이지 않는 전역 공유 엔티티라 멤버십 체크 불필요.

## 배운 점

- "다중 저장"을 구현했다고 "다중 조회"가 따라오지 않는다. 정규화된 `PlaceImage` 테이블과 비정규화된 `Spot.thumbnail_url`이 분리돼 있어, write 경로만 다중화하면 read는 여전히 대표 1장에 묶인다. 새 컬렉션을 도입할 때 **read surface까지 같이 점검**해야 함.
- 적대적 검증(refuter)이 유효했다: "다중 이미지를 리턴하는 GET이 없다"는 주장을 깨려고 전 디렉토리를 뒤졌지만, 클라이언트로 가는 배열 이미지는 전부 **라이브 크롤 결과**(`InstagramCrawlResponse.images`)였지 저장된 `place_images`가 아님을 확인.

## 상태

- 코드 작성 완료, import·라우트 등록·스키마 필드 검증 완료 (`/places/{place_id}/images` 등록 확인).
- **커밋 보류** (사용자 요청) — DB 실데이터 대상 동작 확인(다중 이미지 place로 호출 → N행 정렬 반환) 후 커밋 예정.
