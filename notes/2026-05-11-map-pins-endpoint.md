# 2026-05-11 — 지도 핀 표시용 엔드포인트 (`GET /users/me/pins`)

## 작업 내용

네이버 지도 위에 사용자 저장 spot을 핀으로 표시하기 위한 슬림 응답 엔드포인트 신규 추가.
다중 창고 선택, viewport bbox 필터, visited 필터 지원.

- **신규**: `app/schemas/pin.py`(PinResponse), `app/routers/users.py`에 `list_my_pins` 핸들러 + CSV·bbox 파싱 헬퍼 2개
- **신규 스크립트**: `scripts/_oneoff_check_pins_query.py`(PostGIS dry-run), `scripts/_oneoff_check_pins.py`(핸들러 직접 호출), `scripts/_oneoff_check_pins_data.py`(DB 진단)
- **변경 없음**: 모델·마이그레이션·기존 라우터·`app/main.py`

## 결정 이유 (WHY)

### 1. per-storage vs cross-storage vs 다중 선택
**선택: 다중 선택 가능한 단일 엔드포인트(`?storage_ids=1,3,5`)**.
- 사용자가 직접 "지도에 표시할 창고를 다중 선택"한다고 명시. 단일 창고도 같은 엔드포인트(ID 1개)로 처리되어 분기 불필요.
- per-storage(`/storages/{id}/pins`)면 클라가 N번 호출 후 머지해야 하고, viewport bbox·1000건 캡 의미가 storage 단위로 분산.

### 2. 비멤버 storage_id를 silent drop vs 404
**선택: 404 + `inaccessible_ids` detail**.
- 1차 design critique에서 `_get_member`(`app/routers/storages.py:32-35`)가 이미 비멤버에 404을 명시 — silent drop은 코드베이스 일관성 깨고 디버깅 어려움.
- 정상 클라이언트는 `GET /storages`로 받은 자기 멤버 목록에서 고를 거라 UX 손해 없음. 임의 ID brute-force 우려는 약함(공개 storage 발견 API 부재).
- 부분 거부도 거부 — 26+99999999 보내면 26 데이터도 안 나가고 404. 명시적이 안전.

### 3. 응답 캡 1000건 + 페이지네이션 미도입
**선택: 1001건까지 받아 truncation 감지 → `X-Truncated: true` 헤더**.
- 페이지네이션은 지도 UX와 부적합(한 화면에 다 그려야 함).
- bbox로 자연 제한되지만 사용자가 bbox 안 보낼 때 안전망 없음 → silent failure 방지가 중요. 헤더 한 줄 추가 비용 0.
- `limit(1001)` 트릭으로 별도 COUNT 쿼리 없이 truncation 판정.

### 4. 좌표 추출 — Pydantic computed_field vs SQL `ST_X/ST_Y`
**선택: SQL에서 직접 float 추출, raw row → PinResponse 매핑**.
- PlaceResponse(`app/schemas/place.py:30-42`)는 ORM coordinate 메모리 로드 후 `to_shape(...)` 변환 — 1000건 시 비효율.
- SQL `func.ST_X/ST_Y`로 DB가 float로 반환 → ORM 직렬화 오버헤드 회피. `from_attributes` 불필요.
- 1차 dry-run으로 SQLAlchemy `func.ST_*` 호출이 코드베이스 첫 도입임을 확인 → `_oneoff_check_pins_query.py`로 선행 검증.

### 5. 좌표 없는 spot 처리
**선택: 응답에서 제외**.
- 지도에 핀을 못 박으니 응답에 넣을 의미 없음. 사용자 결정.
- 창고 목록 화면(`GET /storages/{id}/spots`)에서는 그대로 노출되므로 사용자가 "저장한 장소"를 잃지는 않음.

### 6. CSV 파싱 헬퍼 직접 작성
**선택: `_parse_csv_ints`/`_parse_bbox` 헬퍼**.
- FastAPI `Query` 자체는 CSV 미지원(반복 파라미터 `?id=1&id=2`만 가능). 코드베이스 선례 0건.
- query string이 짧고 깔끔(`?storage_ids=1,3,5`) — 클라이언트 친화적이라 helper 비용 정당화.

### 7. 라우터 위치 — 별도 `pins.py` vs `users.py` inline
**선택: `app/routers/users.py`에 inline 추가**.
- 기존 `/users/me/space-dna`(`app/routers/users.py:34`)와 같은 결.
- 핸들러 1개 + 헬퍼 2개 분량 — 별도 파일 분리 정당화 안 됨. `app/main.py` 수정 불필요.

### 8. 핀 클릭 시 인스타 출처 — 핀 응답 vs lazy 로딩
**선택: lazy 로딩(클릭 시 기존 `GET /storages/{id}/spots/{spot_id}`)**.
- 인스타 caption은 길어 핀 응답에 포함 시 페이로드 비대화. 클릭 시점에만 추가 조회.
- 응답에 `spot_id`, `storage_id`를 다 실어 클라가 lazy 호출 URL을 자체 구성 가능.

## 배운 점

- **GIST 인덱스 자동 생성 ≠ 마이그레이션 명시**: `models.py:102` 주석은 "자동 생성"이라고 했지만 마이그레이션 파일에 `op.create_index` 호출이 없음 → GeoAlchemy2가 ORM 초기화 시 SQL을 발행하는 동작에 의존. dry-run에서 `pg_indexes`로 확인하니 운영 DB에 `idx_places_coordinate`가 실재함. 그러나 마이그레이션을 신뢰하려면 명시 추가가 안전.
- **PostgreSQL planner는 작은 테이블에서 GIST 인덱스 무시**: dry-run `EXPLAIN ANALYZE`가 Seq Scan 선택. places 28건 규모에서 인덱스 lookup이 seq scan보다 비싸다고 판단 — 정상. 데이터 늘면 자동 전환.
- **`limit(1001)` truncation 트릭**: 별도 COUNT 쿼리 없이 1001건 페치해서 1000 초과면 잘렸다고 판정. 한 번의 쿼리로 끝나고 페이지네이션도 불필요.
- **Pydantic raw row 매핑**: SQLAlchemy `Row`의 `.spot_id` 같은 라벨 attribute 접근으로 `PinResponse(**)` 없이도 한 줄로 매핑 가능. ORM 객체 거치지 않아 메모리·직렬화 효율 모두 향상.
- **시나리오 검증 시 데이터 진단이 먼저**: 첫 시도에서 user_id=3을 무작정 골랐다가 spot 0건이라 성공 경로 검증 못 함. `_oneoff_check_pins_data.py`로 (user, storage, spot count) 조합을 먼저 본 뒤 user_id=13(spot 21건)로 재실행해 의미 있는 검증 완료. 운영 데이터 검증 전 데이터 분포 진단 패턴 정착.

## 검증 결과 (운영 Supabase, 읽기만)

- 0단계 dry-run: GIST 인덱스 존재 확인 + `func.ST_X/Y/MakeEnvelope` SQLAlchemy 호출 정상 동작 확인
- 단일 창고(user_id=13/storage_id=13): 21건 정상 반환
- 다중 창고(user_id=23/storage_ids=26,27): 3건(2+1) 합쳐서 반환
- visited 필터: true=0, false=21 (운영 DB에 visited spot 0건이라 true 케이스는 빈 결과 자체가 정답)
- bbox 필터: 한국 전체(124,33,132,39)=21, 좁은 영역(0,0,1,1)=0 → PostGIS 정상 작동
- 비멤버 storage_id(99999999)=404 + `inaccessible_ids: [99999999]`
- 부분 비멤버(26+99999999): 26 데이터 누설 없이 404
- 형식 오류: `storage_ids=abc` → 422, `bbox=1,2,3` → 422

## 후속 / 미해결

- **truncation 검증(1001건)**: 운영 DB에 spot 1001건 시드 필요 → destructive write 범위라 미수행. 로컬 docker-compose에서 시드해서 한 번 돌려보면 안전. 또는 코드 리뷰로 `limit(1001)` + `len(rows) > 1000` 가지치기 로직만 검증.
- **클라이언트 통합**: 네이버 지도 JS API에서 받은 좌표로 마커 박는 부분은 백엔드 범위 외. 클라이언트 팀과 응답 형식·헤더(`X-Truncated`) 합의 필요.
- **GIST 인덱스 마이그레이션 명시**: `models.py:102` 주석에 의존 중. 향후 환경 재구축이나 새 PostgreSQL 인스턴스에서 재현 보장하려면 alembic 마이그레이션에 `op.create_index('idx_places_coordinate', 'places', ['coordinate'], postgresql_using='gist')` 추가 검토.
- **카테고리별 마커 아이콘**: 클라가 `Place.category_group`로 마커 분기를 원하면 PinResponse에 한 필드 추가하면 끝.
- **같은 place 중복 핀**: 같은 사용자가 같은 장소를 다른 창고에 저장하면 핀이 겹쳐 표시. 백엔드는 그대로 N개 반환 — 클라가 같은 좌표 핀에 오프셋·묶음 처리.
