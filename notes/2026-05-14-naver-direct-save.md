# 2026-05-14 — 앱 내 네이버지도 저장 → IG 동등 사이드이펙트

## 작업 내용

기존 `POST /places/from-naver`는 Place + naver raw upsert만 하고 끝났다. 사용자가 앱에서 네이버지도로 검색해서 저장해도 storage에는 사진·리뷰·공간 DNA가 모두 빈 상태로 들어왔다 — IG 공유로 저장한 장소와 데이터 품질 격차가 컸음.

새 엔드포인트 `POST /storages/{storage_id}/spots/from-naver`를 추가해 IG 흐름(`/instagram/save`, `/instagram/share`)과 **완전히 동등한 4종 사이드이펙트**를 발화시킨다.

1. Place upsert (`_upsert_naver_place` 재사용)
2. PlaceImage 저장 (image_url 제공 시 Supabase 영구화)
3. Spot 생성
4. 후속 RQ enqueue 2종 (best-effort): `enqueue_blog_fetch_job` + `enqueue_space_dna_analysis`

기존 `/places/from-naver`는 보존 — "장소만 upsert"하는 의미로 두어 책임을 분리한다.

## WHY — 핵심 결정 이유

### 1. IG `create_spot_from_naver`에 분기 추가가 아니라 새 함수로 분리

- IG 흐름은 `instagram_url` 단위 중복 체크 + `PlaceRawData(provider='instagram')` UPDATE 연결이 **본질적**으로 박혀있음. 매뉴얼 저장은 이 둘 다 의미 없음(IG 메타 자체가 없음).
- 한 함수에 `if instagram is None` 분기를 깔면 시그니처가 흐려지고 두 흐름의 의도가 한 곳에 섞임. 새 함수가 가독성↑·테스트↑.
- 대신 정말 공유 가능한 두 부분(`_check_storage_permission`, `_upsert_naver_place`)만 재사용. 이게 적절한 DRY 경계.

### 2. `image_storage` 일반화 — `instagram/{shortcode}/` 경로 박힘 해소

- `upload_instagram_image`는 `_build_storage_path`에 `instagram/...` 경로가 박혀있고 함수명도 IG 한정. 본문 80% 이상은 namespace 무관한 fetch+검증+업로드 코어였음.
- `_upload_to_supabase(image_url, namespace, key)` 내부 코어로 분리하고, `upload_instagram_image` / `upload_naver_place_image` 두 wrapper만 유지.
- 기존 `upload_instagram_image` 시그니처는 그대로 — IG 호출부(`spot_creator.py:244`) 무수정.

### 3. RQ Queue 헬퍼 promote — 두 라우터 공통

- 기존 `_get_rq_queue`는 `instagram.py:168`의 private 헬퍼. 네이버 라우터에서도 같은 패턴이 필요했다.
- 인라인 복붙 vs promote 중에서 promote를 골랐다 — 두 곳에서 쓰는 시점이면 promote가 정석이고, 향후 다른 라우터에서도 재사용 가능. import 한 줄 변경 비용 작다.
- 새 위치: `app/dependencies/queue.py:get_rq_queue` (auth와 같은 dependencies 계층)

### 4. 사진 출처는 클라이언트가 image_url을 body로 전달

- 옵션 비교: (a) image_url body 전달, (b) multipart 사용자 업로드, (c) 사진 없이 진행.
- (a)를 선택. 네이버 장소 상세나 지도 SDK에서 추출한 이미지를 그대로 우리 측 Supabase Storage로 영구화 — IG 흐름과 같은 패턴.
- 사진은 공간 DNA 분석(외부 AI API가 image_url을 입력으로 받음)의 선결조건이므로 강하게 권장하되, 미제공 시에도 Spot 자체는 만들고 DNA 워커는 자체 가드로 skip하게 둠.

### 5. PlaceImage.uploaded_by 채우기

- IG 흐름은 자동 크롤이라 `uploaded_by`를 nullable로 두지만, 매뉴얼 저장은 "이 사용자가 의도적으로 등록"한 행위가 명확하므로 `uploaded_by=current_user.id`를 채워 추적성↑.

### 6. enqueue를 always-fire (already_saved 케이스에도)

- 워커 단 멱등 가드가 잘 되어 있음 (`_should_refresh` 30일, `_already_analyzed`). 라우터에서 분기하지 않고 항상 발화시켜 코드 단순화 + 만약 가드가 잘못된 케이스라도 같은 패턴으로 처리.

## 배운 점

- **"공유 가능한 부분만" 공유하는 게 DRY의 진짜 의미**. `create_spot_from_naver`에 분기 추가는 표면적으로 DRY해 보이지만, 사실은 두 책임을 한 함수에 섞는 anti-pattern. 진짜 공유는 `_upsert_naver_place`처럼 의도가 완전히 동일한 작은 단위에서.
- **함수명·경로에 도메인 키워드가 박히는 게 일반화의 첫 신호**. `upload_instagram_image`, `instagram/{shortcode}/...` 같은 박힘이 두 번째 호출자가 생기는 순간 일반화 청구서로 돌아옴.
- **best-effort enqueue의 try/except 패턴은 한 곳에서 정의**하고 똑같이 복사하는 게 IG-네이버 두 흐름의 동작 일치성을 가장 잘 보장. 추상화하기에는 너무 짧고, 인라인이지만 패턴이 동일.
- **새 엔드포인트 vs 기존 확장**: 의도가 다르면(장소만 upsert vs Spot 포함 저장) 새 엔드포인트가 응답 모양 일관성·클라이언트 호환·문서화 측면에서 모두 깔끔. "엔드포인트 수 줄이기"는 최적화 목표가 아님.

## 본 PR 범위 외(별도 작업)

- `uq_spots_storage_place` partial 인덱스화 — soft-deleted 행과의 unique 충돌 해소 (IG도 동일 이슈)
- `Spot.added_by` ondelete 무결성 (nullable=False + SET NULL)
- 이미지 없이 저장된 spot에 사진 추가 시 DNA 재트리거 훅
- `/places/from-naver`의 옛 `collect_reviews_for_place` BackgroundTasks 경로를 `enqueue_blog_fetch_job`로 통합

## 검증 결과

- `poetry run python -c "from app.main import app; ..."` — 라우트 등록 + 전체 import 통과
- `/storages/{storage_id}/spots/from-naver` POST 라우트 등록 확인, 기존 `/places/from-naver` 동시 유지
- `_build_storage_path('naver', '12345678', 'https://x.com/foo.png')` → `'naver/12345678/foo.png'` 정상
- 회귀: 기존 IG 라우터 4곳의 `_get_rq_queue` 호출 모두 `get_rq_queue`로 정상 치환
