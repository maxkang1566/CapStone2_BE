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

## 코드 리뷰 반영 (2026-06-01, PR #10 — gemini-code-assist, medium)

리뷰의 두 지적 모두 실코드·마이그레이션 확인 후 사실로 검증돼 반영함.

1. **존재 확인 최적화 (범위 내)**: `db.query(Place).filter(...).first()`는 `Place` 전체를
   로드하는데, `Place`엔 PostGIS `Geometry`인 `coordinate` 컬럼이 있어 단순 존재 체크에
   불필요한 오버헤드. 게다가 핸들러에서 `place`는 오직 존재 확인에만 쓰여 재사용 0 →
   `db.query(Place.id).filter(...).first() is not None`으로 교체(순수 이득).
   - 참고: 같은 파일의 다른 4개 조회 핸들러(`get_place` 등)는 여전히 전체 로드 패턴.
     일괄 변경은 본 PR 범위 밖이라 미적용(diff 비대화 방지).
2. **place_images.place_id 인덱스 추가 (리뷰는 범위 외로 표기했으나 반영)**:
   - 검증: 테이블 생성 마이그레이션(`8d6bab21bc7a`)에 FK만 있고 인덱스 없음 확인.
     Postgres는 FK 참조 컬럼에 인덱스를 자동 생성하지 않음 → `WHERE place_id=X`가 seq scan.
   - **선례**: `place_reviews`는 동일 패턴에 `ix_place_reviews_place_id`(`5fe6c16c6978`)를
     이미 만들어 둠. 즉 `place_images`만 누락된 것 → 컨벤션 정합성 차원에서도 추가가 맞음.
   - 수혜자 2곳: 새 GET 엔드포인트 + `space_dna_analyzer._pick_image_urls`(같은 필터).
   - 신규 마이그레이션 `f3a9c1e7b204_add_place_images_place_id_index`.
     모델에도 `__table_args__ = (Index("ix_place_images_place_id", "place_id"),)` 선언해
     autogenerate 드리프트 방지.
   - 검증: `alembic heads` 단일 head(f3a9c1e7b204), 오프라인 SQL
     `CREATE INDEX ix_place_images_place_id ON place_images (place_id)` 확인(prod 미접촉).

## 배운 점 (추가)

- Postgres FK는 인덱스를 자동 생성하지 않는다 — 참조 컬럼으로 자주 필터하는 자식 테이블은
  명시 인덱스가 필요. 같은 코드베이스 안에서도 `place_reviews`엔 있고 `place_images`엔
  없던 불일치가 그 증거. 새 자식 테이블 추가 시 체크리스트로 둘 것.

## 상태

- 엔드포인트 + 리뷰 반영(존재 확인 최적화 + 인덱스 마이그레이션) 완료, 실DB 동작 검증 완료.
- 브랜치 `feat/place-images-get-endpoint`, PR #10. 인덱스 마이그레이션은 배포 시
  `alembic upgrade head`로 적용 필요(미적용 상태여도 엔드포인트 정상 동작 — 인덱스는 성능용).
