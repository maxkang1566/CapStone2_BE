# 창고 멤버 토큰 초대 링크 (storage_invitations)

작업일: 2026-05-11
관련 plan: ~/.claude/plans/quizzical-splashing-kettle.md

## 작업 내용

기획 백로그 1순위 항목인 "토큰 기반 링크 공유 + 수락/거절" 흐름 구현. 기존
`POST /storages/{id}/members`는 user_id로 즉시 추가하는 방식이라 닉네임 검색
한계(카카오 임시 이메일)와 강제 가입 UX 문제가 있었음.

신규 엔드포인트 6종:

- `POST   /storages/{storage_id}/invitations` — 토큰 발급 (owner)
- `GET    /storages/{storage_id}/invitations` — 활성 초대 목록 (owner)
- `DELETE /storages/{storage_id}/invitations/{invitation_id}` — 취소 (owner, 멱등)
- `GET    /invitations/{token}` — 미리보기 (인증 필수)
- `POST   /invitations/{token}/accept` — 멤버 가입 (인증)
- `POST   /invitations/{token}/decline` — 거절 (인증, 204 no-op)

신규 파일: 마이그레이션 `e6b8d2f0a3c5_add_storage_invitations.py`,
모델 `StorageInvitation` (app/models/models.py),
스키마 `app/schemas/invitation.py`,
라우터 `app/routers/invitations.py`.
수정: `app/main.py`에 라우터 등록.

## 결정 이유 (WHY)

### 1) 멀티유저 공유 토큰 (GitHub/Slack 패턴)
한 토큰으로 여러 사용자가 가입 가능. 동일 사용자 중복 가입은 기존
`uq_storage_members_storage_user` 제약이 자동 차단. 단일-사용 모델로 가면
"링크 공유" UX와 충돌한다.

### 2) 거절(decline)은 204 no-op
멀티유저 링크에서 per-user decline 기록은 의미가 모호하다 (서버는 토큰을
누가 가졌는지 모름). 하지만 기획 spec이 "수락/거절 API"를 명시했으므로
엔드포인트는 노출 — 클라이언트의 수락/거절 UI 시맨틱 일관성 보존.
무효 토큰(404)·만료/취소(410)는 명확히 알려서 클라이언트가 안내 메시지를
띄울 수 있게 한다.

### 3) 미리보기는 인증 필수
토큰만 알면 storage 메타(제목, 초대자 닉네임)가 노출되는 것을 막기 위해
`GET /invitations/{token}`도 로그인 요구. 모바일 앱 흐름: 딥링크 클릭 →
로그인/회원가입 → preview API 호출.

### 4) 토큰 평문 저장
`secrets.token_urlsafe(32)` (256-bit entropy). DB 침해는 어차피 게임 오버
동일한 위협 모델 — 해싱은 password reset 같은 짧은 토큰에 의미 있고,
GitHub/Slack 초대 링크도 평문 저장 관행.

### 5) `expires_in_days` 입력 (1~30일, 기본 7)
절대 시각 입력은 클라이언트 시계 동기화 문제 회피. 30일 상한은
링크가 무한히 유효해지지 않게 가드.

### 6) Role 제한: editor / viewer
owner는 transfer 흐름(`PATCH /members/{uid}` role=owner)으로만. 기존
`StorageMemberAddRequest`와 동일 규약 → 권한 모델 단일화.

### 7) 활성 초대 판정: `revoked_at IS NULL AND expires_at > NOW()`
`_check_invitation_active` 헬퍼로 추출. 410 매핑.

### 8) Storage 소프트 삭제 체크 명시
`_get_member`는 멤버십만 검증하므로, `_check_storage_alive` 별도 헬퍼로
`storages.deleted_at IS NOT NULL`인 경우를 404로 차단. 토큰 생성/preview/accept
모두에서 호출.

### 9) Accept 동시성 처리
같은 user가 두 번 거의 동시에 accept를 호출하는 레이스를
`IntegrityError` catch로 409 매핑.

### 10) 매번 새 토큰 발급
owner가 토큰 생성 요청 시 기존 활성 토큰 유무와 무관하게 항상 새로 발급.
의도적 중복 정책 — 이전 토큰 무효화는 명시적 DELETE로.

## 배운 점

1. **FastAPI 라우터를 두 URL 트리에 걸치게 하기**: `/storages/{id}/invitations`와
   `/invitations/{token}`을 모두 같은 라우터 파일에서 처리. APIRouter에 prefix를
   주지 않고 데코레이터에 절대 경로 사용하면 같은 라우터에 두 트리를 묶을 수 있다.
   tag로 그룹핑되어 Swagger UI에서도 "invitations" 섹션에 모인다.

2. **기존 헬퍼 재사용**: `_get_member`와 `_to_member_detail`을 storages 라우터에서
   import해 invitations 라우터에서 그대로 사용. 권한 체크 규약을 한 곳에 모음으로써
   누군가 storages.py의 권한 모델을 바꾸면 invitations에도 자동 반영된다.

3. **소프트 삭제는 멤버십 체크와 별개**: 처음에는 `_get_member`가 storage 소프트
   삭제까지 알아서 거르겠거니 했지만, 실제 구현은 멤버 row 존재 여부만 본다. 멤버 row가
   살아 있고 storage만 deleted_at이 채워진 상태가 가능 — 별도 헬퍼 분리가 옳다.

4. **`TIMESTAMP WITHOUT TIME ZONE` 일관성**: 기존 컬럼들이 naive datetime이라
   `datetime.now(timezone.utc).replace(tzinfo=None)`로 통일. 혼합하면 SQLAlchemy가
   비교 연산에서 경고를 띄우거나 잘못된 결과를 낼 수 있다.

5. **멀티유저 링크에서 decline의 의미**: 단순히 "거절" 기능을 추가하라고 해서
   per-user 거절 기록 테이블을 만들면 overengineering. 위협 모델·UX 흐름을
   먼저 따지고 가장 가벼운 구현(no-op + 시맨틱 엔드포인트 노출)을 선택하는 것이 옳다.
