"""user_dna 옵션 2a 단위 검증.

`rebuild_user_dna`는 DB 의존이라 헬퍼 단위 검증 + 평균 계산 시뮬레이션으로 동작
확인.

`poetry run python -m scripts._oneoff_check_user_dna_option2a` 실행.
"""

from app.services.user_dna import (
    _average_axes,
    _flatten_onboarding_axes,
    _is_valid_axes,
)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    passed = 0
    failed = 0

    def expect(label: str, actual, expected) -> None:
        nonlocal passed, failed
        ok = actual == expected
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}")
        if not ok:
            print(f"        actual:   {actual}")
            print(f"        expected: {expected}")
            failed += 1
        else:
            passed += 1

    # === Part 1: _flatten_onboarding_axes ===
    section("Part 1. _flatten_onboarding_axes (중첩 dict -> 단일 값 평탄화)")

    # 1-1. 온보딩 중첩 dict
    expect(
        "1-1 중첩 dict -> 첫 유형 비율 단일 값",
        _flatten_onboarding_axes(
            {
                "color": {"high": 60, "mild": 40},
                "density": {"dense": 30, "sparse": 70},
                "form": {"fresh": 80, "vintage": 20},
            }
        ),
        {"color": 60.0, "density": 30.0, "form": 80.0},
    )

    # 1-2. 이미 단일 값 (AI 응답) -> 그대로
    expect(
        "1-2 단일 값 -> 그대로",
        _flatten_onboarding_axes({"color": 25.8, "density": 24.79, "form": 25.15}),
        {"color": 25.8, "density": 24.79, "form": 25.15},
    )

    # 1-3. None / 빈 dict
    expect("1-3a None -> None", _flatten_onboarding_axes(None), None)
    expect("1-3b 빈 dict -> None", _flatten_onboarding_axes({}), None)

    # 1-4. 미지 축 (AI 스킴 변경 호환)
    expect(
        "1-4 미지 축 단일 값은 통과, 미지 축 중첩 dict는 무시",
        _flatten_onboarding_axes({"unknown": 33, "weird_nested": {"x": 1}, "color": 50}),
        {"unknown": 33.0, "color": 50.0},
    )

    # === Part 2: _average_axes (평균 계산) ===
    section("Part 2. _average_axes")

    expect("2-1 빈 리스트 -> 빈 dict", _average_axes([]), {})

    expect(
        "2-2 한 row -> 그 자체",
        _average_axes([{"color": 60.0, "density": 30.0, "form": 80.0}]),
        {"color": 60.0, "density": 30.0, "form": 80.0},
    )

    expect(
        "2-3 두 row 평균",
        _average_axes(
            [
                {"color": 60.0, "density": 30.0, "form": 80.0},
                {"color": 20.0, "density": 50.0, "form": 40.0},
            ]
        ),
        {"color": 40.0, "density": 40.0, "form": 60.0},
    )

    # 키 셋 불일치는 공통 키만 평균
    expect(
        "2-4 키 불일치 시 공통 키만",
        _average_axes(
            [
                {"color": 60.0, "density": 30.0},
                {"color": 20.0, "form": 70.0},
            ]
        ),
        {"color": 40.0},
    )

    # === Part 3: 옵션 2a 통합 시나리오 (rebuild_user_dna가 만들어내는 valid 풀 시뮬레이션) ===
    section("Part 3. 옵션 2a 통합 시나리오 (rebuild_user_dna 동작 시뮬레이션)")

    # 시나리오 A: 신규 가입자 (온보딩 안 함) + spot1 visit
    # - existing = None -> is_first_rebuild = False -> 온보딩 풀 추가 X
    # - valid = [spot1]
    spot1 = {"color": 20.0, "density": 50.0, "form": 40.0}
    pool_a = [spot1]
    expect(
        "A. 신규 가입자 + spot1만 -> spot1 그대로",
        _average_axes(pool_a),
        {"color": 20.0, "density": 50.0, "form": 40.0},
    )

    # 시나리오 B: 온보딩 + 첫 visit spot1
    # - existing.total_visits == 0 -> is_first_rebuild = True
    # - 온보딩 평탄화 -> high=60, dense=30, fresh=80
    # - valid = [spot1, onboarding_flat]
    onboarding = {
        "color": {"high": 60, "mild": 40},
        "density": {"dense": 30, "sparse": 70},
        "form": {"fresh": 80, "vintage": 20},
    }
    onb_flat = _flatten_onboarding_axes(onboarding)
    pool_b = [spot1, onb_flat]
    expected_b = {"color": 40.0, "density": 40.0, "form": 60.0}  # (20+60)/2 등
    expect(
        "B. 온보딩 + 첫 visit spot1 -> 둘 평균",
        _average_axes(pool_b),
        expected_b,
    )

    # 시나리오 C: 온보딩 + spot1 + spot2 (두 번째 visit)
    # - 첫 visit 후 user_space_dna.total_visits=1 -> 두 번째 rebuild는 is_first=False
    # - valid = [spot1, spot2] (온보딩 X)
    spot2 = {"color": 80.0, "density": 60.0, "form": 20.0}
    pool_c = [spot1, spot2]
    expected_c = {"color": 50.0, "density": 55.0, "form": 30.0}
    expect(
        "C. 두 번째 rebuild -> spot1+spot2만 (온보딩 빠짐)",
        _average_axes(pool_c),
        expected_c,
    )

    # 시나리오 D: 온보딩만 있고 spot 0건 (단발 호출)
    # - is_first_rebuild = True, 평균 풀 = [온보딩 평탄화]
    pool_d = [onb_flat]
    expected_d = onb_flat
    expect(
        "D. 온보딩만 + spot 0건 -> 온보딩 평탄화 그대로",
        _average_axes(pool_d),
        expected_d,
    )

    # 시나리오 E: 신규 가입(행 없음) + spot 0건
    # existing=None -> is_first=False -> 평균 풀 빈 -> {} 저장 (has_data=false 유도)
    expect(
        "E. 행 없음 + spot 0건 -> 빈 dict",
        _average_axes([]),
        {},
    )

    # === 결과 ===
    print()
    print(f"=== 결과: {passed} PASS / {failed} FAIL ===")


if __name__ == "__main__":
    main()
