# 작업 메모: API 명세서 작성 (2026-05-03)

## 작업 내용

- 구현된 FastAPI 라우트를 전부 훑어 `docs/API_SPECIFICATION.md`에 한글 API 명세서를 작성함.
- 포함 범위: `/`, `/auth`, `/users`, `/storages`, `/storages/{id}/spots`, `/places`(검색·상세·raw-data·from-naver), `/instagram`, `/health`.

## 결정 이유 (WHY)

- **단일 진실 소스**: Swagger(`/docs`)와 병행하되, 클라이언트·기획 공유용으로 읽기 쉬운 표 형태의 정적 문서가 있으면 온보딩과 계약 정리에 유리함.
- **레포 내부 보관**: 외부 위키 없이 Git으로 버전 관리되는 `docs/`에 두어 코드 변경과 함께 MR로 리뷰 가능하게 함.
- **미구현 구분**: `PlaceReview` 등 DB/스키마만 있고 라우터가 없는 항목은 명시적으로 “미노출”로 적어 기대치를 맞춤.

## 배운 점 / 참고

- 인증이 필요한 엔드포인트는 `app/dependencies/auth.py`의 `OAuth2PasswordBearer`로 통일됨.
- 로그인은 JSON이 아니라 **폼 데이터**(`username` = 이메일)라서 명세서에 Content-Type을 명시하는 것이 중요함.
