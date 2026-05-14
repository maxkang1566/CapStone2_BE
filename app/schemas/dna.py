from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlaceSpaceDNAResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    has_data: bool
    mbti_axes: Optional[dict] = None
    ai_summary: Optional[str] = None
    updated_at: Optional[datetime] = None


class UserSpaceDNAResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    has_data: bool
    mbti_axes: Optional[dict] = None
    preferred_vibe_tags: Optional[dict] = None
    total_visits: int = 0
    last_analyzed: Optional[datetime] = None


REQUIRED_AXES: set[str] = {"color", "density", "form"}

# 각 축의 두 유형. 첫 요소 = AI 응답 단일 값이 가리키는 한쪽 비율 (GET 정규화 시 사용).
AXIS_TYPES: dict[str, tuple[str, str]] = {
    "color":   ("high", "mild"),      # 자극 강도
    "density": ("dense", "sparse"),   # 분위기 밀도
    "form":    ("fresh", "vintage"),  # 트렌디함
}

SUM_TOLERANCE = 0.01


class UserSpaceDNAOnboardingRequest(BaseModel):
    mbti_axes: dict[str, dict[str, float]] = Field(
        ...,
        description="3축 비율. 각 축은 두 유형 dict이며 두 값의 합은 100이어야 합니다.",
    )

    @field_validator("mbti_axes")
    @classmethod
    def _validate_axes(
        cls, v: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        axis_keys = set(v.keys())
        if axis_keys != REQUIRED_AXES:
            raise ValueError(
                f"축 키는 정확히 {sorted(REQUIRED_AXES)}여야 합니다. "
                f"받은 키: {sorted(axis_keys)}"
            )
        normalized: dict[str, dict[str, float]] = {}
        for axis, types in v.items():
            expected = set(AXIS_TYPES[axis])
            got = set(types.keys())
            if got != expected:
                raise ValueError(
                    f"{axis} 축의 유형 키는 정확히 {sorted(expected)}여야 합니다. "
                    f"받은 키: {sorted(got)}"
                )
            for t, val in types.items():
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"{axis}.{t}는 number여야 합니다.")
                if not (0.0 <= float(val) <= 100.0):
                    raise ValueError(
                        f"{axis}.{t}는 0~100 범위여야 합니다. (받음: {val})"
                    )
            total = sum(float(types[t]) for t in expected)
            if abs(total - 100.0) > SUM_TOLERANCE:
                raise ValueError(
                    f"{axis} 축의 두 유형 합은 100이어야 합니다. (받음: {total})"
                )
            # 키 순서를 AXIS_TYPES 정의 순서로 정규화.
            normalized[axis] = {t: float(types[t]) for t in AXIS_TYPES[axis]}
        return normalized
