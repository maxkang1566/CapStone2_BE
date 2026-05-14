"""GET /users/me/space-dna 응답 시나리오별 출력 예시."""

import json
from datetime import datetime, timezone

from app.routers.users import _normalize_axes_to_pairs
from app.schemas.dna import UserSpaceDNAResponse


def render(label: str, response: UserSpaceDNAResponse) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))


def main() -> None:
    now = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)

    # 시나리오 A: 신규 가입자 — DB에 user_space_dna 행 자체 없음
    res_a = UserSpaceDNAResponse(
        has_data=False,
        total_visits=0,
        last_analyzed=None,
    )
    render("A. 신규 가입자 (행 없음, GET 호출만)", res_a)

    # 시나리오 B: 온보딩 POST 직후 — 중첩 dict 그대로 저장됨
    stored_b = {
        "color": {"high": 30.0, "mild": 70.0},
        "density": {"dense": 60.0, "sparse": 40.0},
        "form": {"fresh": 80.0, "vintage": 20.0},
    }
    res_b = UserSpaceDNAResponse(
        has_data=True,
        mbti_axes=_normalize_axes_to_pairs(stored_b),
        preferred_vibe_tags=None,
        total_visits=0,
        last_analyzed=now,
    )
    render("B. 온보딩 POST 직후 (중첩 dict로 저장됨)", res_b)

    # 시나리오 C: AI 트리거 후 — 단일 값으로 덮어써짐, 헬퍼가 정규화
    stored_c = {"color": 25.8, "density": 24.79, "form": 25.15}
    res_c = UserSpaceDNAResponse(
        has_data=True,
        mbti_axes=_normalize_axes_to_pairs(stored_c),
        preferred_vibe_tags=None,
        total_visits=5,
        last_analyzed=now,
    )
    render("C. AI 자동 트리거 후 (단일 값 → 헬퍼 정규화)", res_c)

    # 시나리오 D: AI 트리거 발생했으나 PlaceSpaceDNA 분석 없는 장소만 방문
    #              → 빈 dict({}) + total_visits 누적된 행이 있음
    res_d = UserSpaceDNAResponse(
        has_data=False,
        total_visits=3,
        last_analyzed=now,
    )
    render("D. AI 트리거 빈 행 (mbti_axes={}, has_data=false로 정규화)", res_d)


if __name__ == "__main__":
    main()
