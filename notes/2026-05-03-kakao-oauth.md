# 카카오 OAuth 로그인 구현 (2026-05-03)

## 작업 내용
- `POST /auth/kakao` 엔드포인트 추가
- 모바일 카카오 SDK가 발급한 access_token을 받아 카카오 사용자 정보를 조회하고 자체 JWT를 발급
- 신규 가입 시 기본 저장소(`"내 저장소"`)와 owner 멤버 자동 생성

## 변경 파일
- `pyproject.toml` — `httpx (>=0.28.0,<0.29.0)` 추가
- `.env.example` — `KAKAO_REST_API_KEY` 항목 추가 (현재 미사용, 향후 토큰 검증 강화용)
- `app/schemas/user.py` — `KakaoLoginRequest`, `KakaoLoginResponse` 추가
- `app/services/kakao_oauth.py` — 신규, `fetch_kakao_user()`로 카카오 API 호출 분리
- `app/routers/auth.py` — `login_kakao()` 핸들러 추가

## 결정 이유 (WHY)

### 1. 모바일 SDK access_token 방식 채택
- 클라이언트는 모바일 앱(iOS/Android)으로 확정된 상황 (2026-05-03 기획 메모 참조).
- 모바일 카카오 SDK가 이미 OAuth 플로우/redirect_uri 처리를 담당하므로, 백엔드는 토큰 교환을 다시 할 이유가 없다.
- 백엔드가 code → token 교환을 하면 `KAKAO_CLIENT_SECRET`, `REDIRECT_URI` 관리 부담이 늘고, 모바일/웹 redirect 분기까지 떠안게 된다.

### 2. 계정 병합 (kakao_id → email 순서로 매칭)
- 기획 결정: "동일 이메일로 두 계정 존재 시 kakao_id 연결로 병합 처리".
- 우선순위가 중요: 먼저 `kakao_id`로 찾고, 없을 때만 `email` 매칭으로 fallback. 그래야 이미 카카오 가입된 사용자가 다른 이메일을 새로 등록한 경우(자체 가입)에 잘못 병합되지 않는다.
- 병합 발생 시점에 `kakao_id`만 갱신하고 다른 필드(닉네임, 프로필 이미지)는 덮어쓰지 않는다 — 사용자가 직접 설정한 값 보호.

### 3. 이메일 동의 X → 임시 이메일 자동 생성
- 카카오 동의 항목에서 사용자가 이메일을 거절할 수 있다.
- `User.email`은 NOT NULL + UNIQUE 제약이 있으므로, 가입 자체를 막거나 임시값을 넣어야 한다.
- "가입 거절"은 사용자 경험을 해치므로 `kakao_{id}@picklog.local` 형태의 임시 이메일을 부여. 추후 사용자가 프로필 수정에서 변경 가능.
- `.local` TLD는 RFC 6762로 외부에 절대 라우팅되지 않으므로 메일 발송 실수 방지.

### 4. `httpx` 선택 (requests 대신)
- FastAPI는 async-first인데 라우터에 `async def`를 쓰면서 동기 `requests`를 호출하면 이벤트 루프가 블로킹된다.
- 카카오 API 호출은 ~수백 ms 지연이 가능하므로 `async`/`httpx` 조합이 자연스럽다.

### 5. `httpx.AsyncClient`를 매 요청마다 생성
- Picklog는 트래픽 규모가 작고, 인증은 빈번한 호출이 아니다.
- 풀링 최적화를 위해 lifespan에 클라이언트를 싱글톤으로 두는 방식은 추후 트래픽이 커지면 도입.

### 6. `is_new_user` 응답 플래그 포함
- 모바일에서 첫 로그인 시 온보딩 화면을 띄울 수 있도록 명시적인 플래그 제공.
- `created_at`을 비교하는 우회 방법보다 의도가 명확하다.

## 배운 점
- DB의 `users.kakao_id`는 이미 `unique=True, nullable=True`로 준비되어 있어서 마이그레이션이 불필요했다. 미리 컬럼을 만들어둔 것이 결과적으로 작업을 단축.
- `User.password`도 `nullable=True`라 소셜 가입 시 별도 분기 없이 `password=None`으로 두면 된다. (단, 로그인 라우터의 `if not user.password` 체크 덕분에 비밀번호 없는 계정으로 이메일 로그인 시도 시 자연스럽게 401 처리됨.)
- 모바일 앱 백엔드의 OAuth는 웹 OAuth와 패턴이 다르다 — 백엔드는 토큰 검증/사용자 정보 조회만 하고 OAuth 플로우 자체는 클라이언트 SDK가 책임진다.

## 검증 (수동)
실제 카카오 access_token이 필요하므로 자동 테스트는 미작성. Swagger(`/docs`)에서:
1. 신규 가입 (이메일 동의 O) → `is_new_user=true`, 기본 저장소 1개 생성 확인
2. 같은 토큰 재호출 → `is_new_user=false`
3. 동일 이메일 자체가입 후 카카오 호출 → `users.kakao_id` 연결 확인
4. 이메일 동의 X → `kakao_{id}@picklog.local`로 가입 성공
5. 잘못된 토큰 → 401 응답
