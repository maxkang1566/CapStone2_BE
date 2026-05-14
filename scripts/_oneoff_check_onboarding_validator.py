"""온보딩 스키마 validator 단위 검증.

`poetry run python scripts/_oneoff_check_onboarding_validator.py` 실행.
"""

from app.schemas.dna import UserSpaceDNAOnboardingRequest


def _case_name(idx: int, label: str) -> str:
    return f"[case{idx}] {label}"


def main() -> None:
    cases_passed = 0
    cases_failed = 0

    # 1. 정상 입력
    try:
        ok = UserSpaceDNAOnboardingRequest(
            mbti_axes={
                "color": {"high": 30, "mild": 70},
                "density": {"dense": 60, "sparse": 40},
                "form": {"fresh": 80, "vintage": 20},
            }
        )
        print(_case_name(1, "정상 입력 PASS"), "→", ok.mbti_axes)
        cases_passed += 1
    except Exception as e:
        print(_case_name(1, "FAIL"), "→", repr(e))
        cases_failed += 1

    def _expect_422(idx: int, label: str, payload: dict) -> None:
        nonlocal cases_passed, cases_failed
        try:
            UserSpaceDNAOnboardingRequest(mbti_axes=payload)
            print(_case_name(idx, label), "FAIL — 422 안 발생")
            cases_failed += 1
        except Exception as e:
            msg = str(e).splitlines()[0]
            print(_case_name(idx, label), "PASS →", msg[:100])
            cases_passed += 1

    # 2. 축 키 누락
    _expect_422(
        2,
        "축 키 누락 (color만)",
        {"color": {"high": 30, "mild": 70}},
    )

    # 3. 축 키 추가
    _expect_422(
        3,
        "축 키 추가 (extra)",
        {
            "color": {"high": 30, "mild": 70},
            "density": {"dense": 60, "sparse": 40},
            "form": {"fresh": 80, "vintage": 20},
            "extra": {"a": 50, "b": 50},
        },
    )

    # 4. 유형 키 오류 (bright는 정의되지 않은 유형)
    _expect_422(
        4,
        "유형 키 오류 (bright)",
        {
            "color": {"bright": 60, "mild": 40},
            "density": {"dense": 60, "sparse": 40},
            "form": {"fresh": 80, "vintage": 20},
        },
    )

    # 5. 합 != 100
    _expect_422(
        5,
        "합!=100 (color high=60+mild=50=110)",
        {
            "color": {"high": 60, "mild": 50},
            "density": {"dense": 60, "sparse": 40},
            "form": {"fresh": 80, "vintage": 20},
        },
    )

    # 6. 범위 초과
    _expect_422(
        6,
        "범위 초과 (high=-10)",
        {
            "color": {"high": -10, "mild": 110},
            "density": {"dense": 60, "sparse": 40},
            "form": {"fresh": 80, "vintage": 20},
        },
    )

    # 7. 타입 오류 (string)
    _expect_422(
        7,
        "타입 오류 (high='high')",
        {
            "color": {"high": "high", "mild": 40},
            "density": {"dense": 60, "sparse": 40},
            "form": {"fresh": 80, "vintage": 20},
        },
    )

    # 8. 부동소수점 합 (33.33 + 66.67 = 100.00, ±0.01 안)
    try:
        ok2 = UserSpaceDNAOnboardingRequest(
            mbti_axes={
                "color": {"high": 33.33, "mild": 66.67},
                "density": {"dense": 60, "sparse": 40},
                "form": {"fresh": 80, "vintage": 20},
            }
        )
        print(_case_name(8, "부동소수점 ±0.01 허용 PASS"), "→", ok2.mbti_axes["color"])
        cases_passed += 1
    except Exception as e:
        print(_case_name(8, "FAIL"), "→", repr(e))
        cases_failed += 1

    # === GET 정규화 헬퍼 검증 ===
    from app.routers.users import _normalize_axes_to_pairs

    # 9. AI 단일 값 → 중첩 dict 변환 (소수 둘째 자리 반올림)
    result = _normalize_axes_to_pairs(
        {"color": 25.8, "density": 24.79, "form": 25.15}
    )
    expected = {
        "color": {"high": 25.8, "mild": 74.2},
        "density": {"dense": 24.79, "sparse": 75.21},
        "form": {"fresh": 25.15, "vintage": 74.85},
    }
    if result == expected:
        print(_case_name(9, "AI 단일 값 → 중첩 dict 변환 PASS"))
        cases_passed += 1
    else:
        print(_case_name(9, "FAIL"), "→ 결과:", result, " 기대:", expected)
        cases_failed += 1

    # 10. 이미 중첩 dict면 그대로 통과
    nested = {"color": {"high": 30.0, "mild": 70.0}}
    result2 = _normalize_axes_to_pairs(nested)
    if result2 == nested:
        print(_case_name(10, "중첩 dict 그대로 통과 PASS"))
        cases_passed += 1
    else:
        print(_case_name(10, "FAIL"), "→", result2)
        cases_failed += 1

    # 11. 빈 dict / None
    if _normalize_axes_to_pairs(None) is None and _normalize_axes_to_pairs({}) is None:
        print(_case_name(11, "None/빈 dict → None PASS"))
        cases_passed += 1
    else:
        print(_case_name(11, "FAIL"))
        cases_failed += 1

    # 12. 알 수 없는 축은 그대로 노출 (AI 키 변경 호환성)
    result4 = _normalize_axes_to_pairs({"unknown_axis": 50, "color": 30})
    if result4 == {"unknown_axis": 50, "color": {"high": 30.0, "mild": 70.0}}:
        print(_case_name(12, "알 수 없는 축 그대로 노출 PASS"))
        cases_passed += 1
    else:
        print(_case_name(12, "FAIL"), "→", result4)
        cases_failed += 1

    print()
    print(f"=== 결과: {cases_passed} PASS / {cases_failed} FAIL ===")


if __name__ == "__main__":
    main()
