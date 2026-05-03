# 네이버 지도 기반 장소 Upsert 구현 (2026-05-02)

## 작업 내용

`POST /places/from-naver` 엔드포인트 추가.
앱에서 네이버 지도 SDK로 선택한 장소를 스토리지에 추가하기 전, Place 레코드를 찾거나 생성하는 upsert 흐름.

수정 파일:
- `app/schemas/place.py` — NaverPlaceUpsertRequest, NaverPlaceUpsertResponse 추가
- `app/routers/places.py` — POST /places/from-naver 엔드포인트
- `app/models/models.py` — PlaceRawData.__table_args__ 추가
- `migrations/versions/a3f8b2c1d947_add_naver_upsert_index.py` — partial unique index 신규 생성

## 결정 이유 (WHY)

**2단계 플로우 채택 (from-naver → spots)**
원스텝(`/storages/{id}/spots/with-place`) 방식 대신 분리한 이유:
- place upsert 로직과 spot 생성 로직의 단일 책임 원칙 유지
- 동일 장소를 여러 스토리지에 추가할 때 place_id 재사용 가능 (Place 중복 삽입 방지)
- 기존 `spots.py` 변경 없음

**partial unique index 선택**
`place_raw_data(provider, provider_place_id)` 조합에 인덱스 걸 때 `WHERE provider_place_id IS NOT NULL` 조건 부여한 이유:
- provider_place_id가 NULL인 레코드(인스타그램 등 다른 경로로 수집된 raw_data)는 중복 방지 대상이 아님
- NULL 컬럼에 unique 제약을 걸면 NULL끼리는 충돌하지 않지만(PostgreSQL 동작), 의도가 불명확해짐
- place_reviews 테이블의 `uq_place_reviews_place_provider_ext_id`와 동일 패턴 — 프로젝트 내 일관성 유지

**IntegrityError 핸들링**
rollback 후 재조회 방식:
- 동시 요청이 동일 naver_place_id로 들어올 때 한 쪽만 INSERT 성공하고 나머지는 IntegrityError
- 재조회로 이미 생성된 레코드를 가져와 `created=False` 반환 → 멱등성 보장

**WKTElement 사용**
`WKTElement(f"POINT({lon} {lat})", srid=4326)` — PostGIS WKT 표준에서 X=경도, Y=위도 순서.
WKBElement(DB에서 읽을 때)와 WKTElement(쓸 때) 모두 `geoalchemy2.shape.to_shape()`로 변환 가능하므로 PlaceResponse computed_field와 호환됨.

## 배운 점

- Alembic partial unique index는 `op.create_index(..., postgresql_where=sa.text("..."))` 형식
- `downgrade`에도 동일한 `postgresql_where` 인자를 전달해야 인덱스가 정확히 식별되어 드랍됨
- FastAPI 라우터에서 `/from-naver` 같은 고정 경로는 `/{place_id}` 파라미터 경로보다 앞에 위치해야 하지만, HTTP 메서드가 다르면(POST vs GET) 충돌 없음
- curl에서 한글 포함 JSON을 `-d` 인라인으로 넘기면 파싱 오류 발생 → `--data-binary @파일` 방식 사용
