# 장소 Upsert 및 중복 방지 기능 구현 (2026-05-03)

## 작업 내용

### 1. 네이버 지도 장소 Upsert — `POST /places/from-naver`

앱 UI에서 네이버 지도 SDK로 선택한 장소를 스토리지에 추가하는 플로우를 지원.
`naver_place_id`를 기준으로 이미 DB에 있는 장소면 재사용, 없으면 신규 생성.

**흐름:**
```
앱 → POST /places/from-naver  →  { place_id, created: bool, place }
   → POST /storages/{id}/spots →  SpotResponse
```

**수정 파일:**
- `app/schemas/place.py`: `NaverPlaceUpsertRequest`, `NaverPlaceUpsertResponse` 추가
- `app/routers/places.py`: `POST /places/from-naver` 엔드포인트 추가
  - `WKTElement(f"POINT({lng} {lat})", srid=4326)` 로 좌표 변환
  - 동시 요청 충돌(IntegrityError) → rollback 후 재조회 패턴 적용
- `app/models/models.py`: `PlaceRawData.__table_args__` 추가 (uq_place_raw_data_provider_pid)
- `migrations/versions/a3f8b2c1d947_add_naver_upsert_index.py`: partial unique index 생성

**응답 필드 `created`의 역할:**
- `true`: 신규 장소 → 앱에서 "새 장소 등록됨" 메시지 표시 가능
- `false`: 기존 장소 재사용 → 앱에서 "이미 픽로그에 있는 장소" 표시 가능

---

### 2. Instagram 장소 중복 방지 (location_id 기반)

다른 인스타그램 게시물이지만 같은 장소를 태그한 경우 Place가 중복 생성되던 문제 개선.
Instagram 게시물 HTML의 `<script>` 태그에서 `location_id` 추출을 시도.

**수정 파일:**
- `app/services/instagram_crawler.py`: `_extract_location_from_scripts()` 메서드 추가
  - 정규식: `"location":\{"id":"(\d+)","name":"([^"]+)"}` 패턴 탐색
  - 실패 시 기존 OG description 파싱으로 fallback
- `app/schemas/instagram.py`:
  - `InstagramCrawlResponse`에 `instagram_location_id: str | None` 추가
  - `InstagramSaveResponse` 신규 추가 (`spot`, `already_saved`, `place_created`)
- `app/routers/instagram.py`:
  - `POST /instagram/save` 응답 모델 `SpotResponse` → `InstagramSaveResponse` 변경
  - location_id 기반 upsert 로직 도입

**저장 로직 변경:**
```
기존: 항상 새 Place 생성
변경:
  location_id 추출됨
    └─ 기존 PlaceRawData 있음 → 기존 Spot 있음 → already_saved=True 반환 (200)
                              → 기존 Spot 없음 → 새 Spot 생성 (201)
    └─ 기존 PlaceRawData 없음 → 새 Place 생성 → 새 Spot 생성 (201)
  location_id 없음 → 기존처럼 새 Place 생성 (fallback)
```

**`already_saved` 응답 활용:**
- HTTP 200 + `already_saved: true` → 앱에서 "이미 저장된 장소입니다. 기존 스팟으로 이동하시겠어요?" UI
- `spot` 객체에 기존 Spot 정보가 담겨 있어 앱이 바로 해당 스팟으로 네비게이션 가능

---

### 3. DB 인덱스 추가

`place_raw_data` 테이블에 partial unique index 추가:
```sql
CREATE UNIQUE INDEX uq_place_raw_data_provider_pid
  ON place_raw_data (provider, provider_place_id)
  WHERE provider_place_id IS NOT NULL;
```

- `WHERE provider_place_id IS NOT NULL` 조건: 기존 Instagram NULL 데이터 보호
- Naver/Instagram 동일한 upsert 패턴 공유
- `place_reviews` 테이블의 partial unique index와 동일한 설계 패턴 (일관성)

---

## 결정 이유 (WHY)

### Q. 왜 2단계(upsert + spot 생성 분리)로 설계했나?
- Place upsert 로직과 Spot 생성 로직을 분리 → 단일 책임
- 동일 Place를 다른 스토리지에 추가할 때 place_id 재사용 가능 (upsert 재호출 불필요)
- 기존 `POST /storages/{id}/spots` 변경 없음 → 기존 인스타그램 플로우와 충돌 없음

### Q. 왜 크로스 플랫폼 중복(네이버 ↔ 인스타)은 허용했나?
- 두 플랫폼의 장소 ID는 완전히 다른 체계 → 매핑 테이블이나 좌표 근접 매칭 필요
- 좌표 근접 매칭은 오탐 가능성 있고 구현 복잡도 증가
- MVP에서는 허용하고, 나중에 Place 병합 기능으로 해결하는 것이 더 실용적

### Q. 왜 `provider_place_id`를 `places` 테이블이 아닌 `place_raw_data`에 넣었나?
- `places` 테이블에 `naver_place_id` 컬럼을 추가하면 제공자(provider)에 종속
- Kakao, Google 등 다른 지도 API 추가 시 컬럼이 계속 늘어남
- `place_raw_data`는 이미 `provider + provider_place_id` 구조 → 확장성 좋음

---

## 배운 점 / 주의사항

### Instagram location_id 추출 한계
- **비인증 상태에서는 Instagram이 `<script>` 태그의 위치 JSON을 숨김**
- Playwright로 JS를 실행해도 로그인 없이는 location_id 데이터에 접근 불가
- 현재 `provider_place_id=None`으로 저장되어 location_id 기반 중복 방지는 작동하지 않음
- **해결 방법 (향후):**
  1. Instagram 로그인 쿠키를 Playwright에 주입 (쿠키 만료/계정 차단 리스크 있음)
  2. 인스타그램 캡션/해시태그에서 장소명 추출 → 네이버 API 검색 → naver_place_id 기준 upsert
  3. 앱 단에서 사용자가 직접 네이버 지도 장소와 연결하는 UX

### Playwright 핫리로드 이슈
- `run_dev.py`로 서버 실행 시 파일 변경 → hot-reload → 새 프로세스 생성
- Playwright 브라우저는 이전 프로세스에서 생성됨 → `greenlet.error: cannot switch to a different thread`
- **코드 수정 후 반드시 서버를 완전히 재시작해야 함**
- 로컬 테스트 시 `poetry run uvicorn app.main:app --port 8000` (핫리로드 없이) 사용 권장

### IntegrityError 동시 요청 처리 패턴
```python
# check-then-act 대신 try-insert-on-conflict-fallback 패턴 사용
try:
    place = Place(...); db.flush()
    raw_data = PlaceRawData(...); db.commit()
    return Response(created=True)
except IntegrityError:
    db.rollback()
    existing = db.query(PlaceRawData).filter(...).first()
    return Response(created=False, place=existing.place)
```
- DB unique index가 원자성 보장 → 애플리케이션 레벨 체크 불필요
- `places` upsert와 `instagram/save` 양쪽에 동일 패턴 적용

---

## 크로스 플랫폼 중복 처리 현황

| 케이스 | 처리 |
|---|---|
| 네이버에서 같은 장소 두 번 저장 | 차단 (naver_place_id 기준) |
| 인스타그램 다른 게시물, 같은 장소 | 로직은 구현됨. 단, location_id 추출이 실제로 작동해야 효과 있음 |
| 네이버 장소 = 인스타그램 장소 | 허용 (향후 병합 기능으로 해결) |
