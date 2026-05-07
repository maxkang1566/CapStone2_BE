# API 명세서(`docs/API_SPECIFICATION.md`) 갱신 (2026-05-04)

## 작업 내용
최근 추가/변경된 엔드포인트와 스키마를 코드와 동기화.

### 추가
- `POST /auth/kakao` — 카카오 OAuth 로그인 엔드포인트 신규 문서화
  - 요청 `KakaoLoginRequest` (`access_token`)
  - 응답 `KakaoLoginResponse` (`access_token`, `token_type`, `is_new_user`)
  - 동작(매칭 우선순위, 기본 저장소 자동 생성, 임시 이메일) 및 오류(401/502) 명시
- `KakaoLoginResponse`, `InstagramSaveResponse`를 스키마 요약 섹션에 신규 등록
- 인증 공통 안내에 `/auth/kakao`도 토큰 발급 경로로 추가
- `InstagramCrawlResponse`에 `instagram_location_id` 필드 추가 + 비고

### 전면 교체
- `POST /instagram/save` 섹션 — 방식 C 반영
  - 요청 본문을 2개 필드(`url`, `storage_id`) → 13개 필드로 교체
  - 응답을 `SpotResponse` → `InstagramSaveResponse` (`spot`, `already_saved`, `place_created`)로 교체
  - 오류 표 정정: 크롤링 의존(`400/504/500`) 항목 제거, `404`/`403`/`409`만 유지
  - 동작 단계 4단계 명시 (storage 자동 선택 → 중복 검사 → Place upsert → 동일 Place 스팟 재사용)

### 메타
- 미노출 사항에 카카오 OAuth가 모바일 SDK 방식 한정이라는 점 명시
- 미노출 사항에 미구현 기능(반경 검색/멤버 초대/공개 피드/장소 DNA) 추가
- 문서 정합성 푸터에 "최종 갱신: 2026-05-04" + 변경 요약 명시

## 결정 이유 (WHY)

- **요청 본문을 표 13행으로 그대로 펼친 이유**: `InstagramSaveRequest`는 이제 `/crawl` 결과 + 네이버 장소 데이터의 결합 스키마라 일종의 통합 contract다. 모바일 클라이언트가 이 표 하나만 보고도 호출 가능해야 하므로 별도 schema 참조가 아닌 본문에 직접 모든 필드를 표기.
- **`already_saved` / `place_created`를 동작 설명과 응답 표 양쪽에 중복 기재한 이유**: 클라이언트가 두 플래그를 보고 토스트/내비게이션을 분기하는 경우가 잦아 의미 강조. (이미 저장됨 → 알림 + 기존 페이지로, 신규 → 성공 화면)
- **카카오 임시 이메일 형식을 명세에 명시한 이유**: 모바일/QA 팀이 `kakao_*@picklog.local` 패턴을 보고 "이메일 동의 미수신 케이스"임을 즉시 식별할 수 있도록.
- **라우터/스키마 코드는 건드리지 않음**: 문서만 갱신. 실제 동작은 변경 없음.

## 배운 점

- 명세서 audit는 "라우터에 있는데 명세서에 없는 것" + "명세서에 있는데 라우터에 없는 것" + "양쪽에 있지만 스키마가 갈라진 것" 세 축으로 보면 누락이 적다.
- `POST /instagram/save`처럼 한 번에 여러 도메인(인스타+네이버+spot)을 결합하는 엔드포인트는 동작 설명을 단계 번호로 풀어두면 클라이언트가 mental model 잡기 쉽다.
- 명세서에 "최종 갱신 날짜 + 한 줄 변경 요약"을 푸터에 적어두면 모바일/AI팀이 diff 없이도 "이 버전이 맞는가" 확인 가능.

## 변경 파일
- `docs/API_SPECIFICATION.md` (430 → 512 라인, +82)
- `notes/2026-05-04-api-spec-update.md` (이 파일)
