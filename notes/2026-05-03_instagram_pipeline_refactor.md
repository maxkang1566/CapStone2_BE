# Instagram 파이프라인 정비 - 방식 C 적용 (2026-05-03)

## 작업 내용

기존 `POST /instagram/save`의 내부 재크롤링 방식을 제거하고, 클라이언트가 사전에 `/crawl`로 얻은 데이터 + 네이버 지도에서 선택한 장소 정보를 함께 전달하는 방식(방식 C)으로 교체.

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/schemas/instagram.py` | `InstagramSaveRequest` 완전 교체 |
| `app/routers/instagram.py` | `save_instagram_spot` 로직 교체 |

---

## 새 플로우

```
[클라이언트]
1. POST /instagram/crawl  →  { caption, thumbnail_url, ... }  (변경 없음)
2. 앱에서 네이버 지도로 장소 선택
3. POST /instagram/save   →  SpotResponse
   (크롤 결과 + 네이버 장소 데이터를 한 번에 전송)
```

---

## 새 InstagramSaveRequest 필드

```python
instagram_url: HttpUrl        # 인스타 게시물 URL (필수)
caption: str | None           # /crawl 결과 캡션
thumbnail_url: str | None     # /crawl 결과 대표 이미지

naver_place_id: str           # 네이버 장소 ID (필수 — 장소 연결 보장)
place_name: str               # 장소명 (필수)
place_address: str | None
latitude: float | None
longitude: float | None
category_group: str | None
place_raw_payload: dict | None

storage_id: int | None        # 미제공 시 기본 저장소
user_memo: str | None
user_rating: float | None
```

---

## 새 save 로직 요약

1. storage_id 결정 (기존 `_get_default_storage_id` 재사용)
2. 창고 접근 권한 확인 (owner/editor)
3. `instagram_url` 중복 체크 → 409
4. `naver_place_id` 기준 Place upsert (IntegrityError → rollback → 재조회 패턴)
5. 동일 Place의 Spot이 이 storage에 이미 있으면 `already_saved=True` 반환
6. Instagram PlaceRawData 저장 (캡션/이미지 URL 보관)
7. 썸네일 PlaceImage 저장
8. Spot 생성 → commit → 201 반환

---

## 제거된 것

- `save_instagram_spot`의 `async` → 일반 `def`로 변경
- `PlaywrightManager` 의존성 save에서 완전 제거 (`crawl` 전용으로 유지)
- `InstagramCrawler` 호출 제거 (save에서)
- "이름 없음" Place 자동 생성 로직 제거

---

## 테스트 결과

| 시나리오 | 기대 | 결과 |
|---|---|---|
| 신규 저장 | 201, already_saved=false, place_created=true | ✅ |
| 같은 naver_place_id + 다른 instagram_url | 201, already_saved=true | ✅ |
| 같은 instagram_url 재요청 | 409 | ✅ |
| naver_place_id 누락 | 422 | ✅ |

---

## 결정 이유 (WHY)

### Q. 왜 서버 내 재크롤링을 제거했나?
- 기존 `save`는 매번 Playwright로 Instagram을 재크롤링 → 서버 부하, 느린 응답
- 비인증 상태에서 `location_id` 추출이 항상 None → 중복 방지 미작동
- "이름 없음" Place가 DB에 쌓이는 쓰레기 데이터 발생
- 클라이언트가 `/crawl`에서 이미 데이터를 가지고 있으므로 재크롤링 불필요

### Q. 왜 naver_place_id를 필수로 만들었나?
- 기획 결정: "장소 연결은 반드시 네이버 지도 검색으로만 처리 (자동 매핑 없음)"
- 네이버 place_id 없이는 장소 정규화 보장 불가 → 중복 방지 미작동
- 필수 필드로 강제하면 422로 명확하게 거부 가능

### Q. already_saved 판단 기준이 왜 instagram_url이 아닌 place_id인가?
- 같은 장소를 서로 다른 Instagram 게시물로 저장하는 경우 모두 중복으로 처리
- instagram_url은 게시물 중복 방지용 (409), place_id는 장소 중복 감지용 (already_saved)
- 두 검사가 다른 목적을 가짐

---

## 주의사항

- `/crawl` 엔드포인트는 변경 없음 (async + PlaywrightManager 유지)
- 서버 재시작 없이 코드 변경 → 기존 프로세스가 구 코드 계속 실행 (포트 8000 점유)
  → 테스트 전 반드시 포트 8000 프로세스 종료 후 재시작
