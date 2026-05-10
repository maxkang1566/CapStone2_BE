# 공간 DNA 조회 API 2종 (2026-05-10)

## 작업 내용

- 신규 엔드포인트 2개 (둘 다 인증 필수):
  - `GET /places/{place_id}/space-dna` — 장소 공간 DNA 조회
  - `GET /users/me/space-dna` — 내 공간 DNA 조회
- 신규 스키마 모듈 `app/schemas/dna.py`:
  - `PlaceSpaceDNAResponse`: `has_data`, `mbti_axes`(dict), `ai_summary`, `updated_at`
  - `UserSpaceDNAResponse`: `has_data`, `mbti_axes`, `preferred_vibe_tags`, `total_visits`, `last_analyzed`
- 라우터 수정: `app/routers/places.py`(맨 끝에 추가), `app/routers/users.py`(`PUT /me` 직후)
- 마이그레이션 없음 — 모델 (`PlaceSpaceDNA`, `UserSpaceDNA`)은 이미 정의됨.

## 결정 이유 (WHY)

### 왜 빈 데이터일 때 200 + `has_data=false` 응답인가 (404 아님)
- 신규 가입자는 `user_space_dna` 행이 항상 없음 — 정상 케이스. 404로 처리하면 모든 클라이언트가
  매 호출 try/catch를 강제당하고 옵저버빌리티에서 정상 흐름이 에러로 카운트됨.
- 장소 DNA도 시드 25건만 있고 대부분 Place는 데이터 없는 상태이므로 같은 논리 적용.
- 클라이언트 분기: `if (res.has_data) showDna() else showEmptyState()`.

### 왜 `mbti_axes`를 dict 그대로 노출했는가 (4축 명시 필드 분리 아님)
- AI팀이 `confidence`처럼 추가 키를 도입하거나 축 명세를 조정해도 백엔드 스키마/마이그레이션
  수정 불필요.
- OpenAPI 스키마 타입 안전성은 잃지만, 동결 키 명세는 `notes/2026-05-09-ai-team-handoff.md`와
  `seeds/README.md`에서 관리되므로 클라이언트가 참조할 단일 출처가 있음.
- 동결된 키: `busy_calm / calm_flashy / modern_vintage / premium_value` (-1.0~1.0) + `confidence` (0~1).

### 왜 권한이 인증만 요구하는가 (storage 멤버십 검사 X)
- Place는 글로벌 마스터 데이터 — 비공개 storage에 들어 있다고 해서 Place 자체 정보가 비밀은
  아님. 기존 `GET /places/{id}`도 인증만 요구하므로 `space-dna`도 통일.
- User DNA는 `/me` 한정으로 노출 — 타인 DNA 조회 엔드포인트는 이번에 만들지 않음 (요구사항 없음).

### 왜 `mbti_axes or None`로 빈 dict를 None으로 정리했는가
- `PlaceSpaceDNA.mbti_axes`는 `server_default='{}'`로 정의되어 AI팀이 행만 만들고 분석 전이면
  빈 dict가 응답에 노출됨. 클라이언트 입장에서 빈 dict는 "분석은 시작됐지만 값이 없음"인지
  "그냥 빈 데이터"인지 분간 어려움 → null로 통일.

## 배운 점

- FastAPI 라우트 등록은 모듈 import 시점에 결정되므로, `python -c "from app.routers import places; [r.path for r in places.router.routes]"`로 라우트가 실제 붙었는지 빠르게 확인 가능. 서버 띄우지 않고도 등록 누락/오타 체크 가능.
- Pydantic v2의 `ConfigDict(from_attributes=True)`는 ORM 객체에서 필드를 매핑할 때만 동작 —
  `None` 객체에서 모델을 만들 수는 없으므로 빈 데이터 케이스는 라우터에서 직접 `has_data=False` +
  나머지 필드 None으로 응답을 조립해야 함.
- 새 라우트가 기존 동적 세그먼트(`/{place_id}`)와 충돌하지 않는지 확인할 때, FastAPI는 등록 순서를
  따라 매칭하므로 정적 세그먼트(`/me/space-dna`)가 동적 세그먼트(`/{user_id}`)보다 먼저 와야 함.
  현재 `users.py`에는 `/{user_id}` 라우트가 없어 무관했지만, 향후 추가 시 주의.

## 검증

```bash
poetry run python -c "from app.routers import places, users; print([r.path for r in places.router.routes if 'space-dna' in r.path]); print([r.path for r in users.router.routes if 'space-dna' in r.path])"
# → ['/places/{place_id}/space-dna']
# → ['/users/me/space-dna']
```

서버 기동 + Swagger UI 시나리오 검증은 사용자 액션으로 미룸 (시드된 place_id 1건 + DNA 없는 place_id 1건 양 케이스).

## 후속

- 유저 DNA 자동 업데이트 (방문 체크인 시 `user_space_dna` upsert + history 적재) — 다음 우선순위.
- 장소 DNA `confidence` 임계치 미만 시 응답에서 마스킹할지는 프론트팀 합의 후 결정.
