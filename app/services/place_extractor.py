"""인스타 캡션에서 장소·주소 후보 텍스트를 추출한다.

이 모듈은 추출 정확도를 100%로 보장하지 않는다. `instagram_share`의 자동 매핑은
네이버 검색 결과가 **유일한 naver_place_id로 수렴할 때만** 자동 저장하므로,
오탐 가능성이 있는 후보를 다소 포함해도 안전하다(수동 폴백으로 떨어짐).

추출 전략(우선순위 순):
1. `📍`/`주소`/`위치`/`Location` 마커 뒤 텍스트
2. 시·도 + 구·군 + 로·길 형태 주소 패턴
3. 캡션 첫 줄에서 `|`/`-`/`:` 구분자 뒤 상호명 (예: "Store | 불란서와이너리")
"""
from __future__ import annotations

import re
from typing import Iterable

# 라인 단위 마커 패턴
_PIN_RE = re.compile(r"📍\s*([^\n📍]+)")
_LABEL_RE = re.compile(r"(?im)^(?:주소|위치|Location|Address)\s*[::\-]\s*(.+?)\s*$")

# 시·도 + 구·군·시 + 로·길 형태 도로명/지번 주소
# 예: '서울시 영등포구 경인로108길 7 1층', '경기 성남시 분당구 정자일로 1'
_REGION_PREFIXES = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
)
_ADDRESS_RE = re.compile(
    r"(?:" + "|".join(_REGION_PREFIXES) + r")"
    r"(?:특별시|광역시|특별자치시|특별자치도|도|시)?"
    r"\s*\S{1,30}?(?:구|군|시)"
    r"\s*\S{1,30}?(?:로|길|동|읍|면)"
    r"\s*\d+(?:[-]\d+)?(?:\s*\d+층)?"
)

# 캡션 첫 줄 구분자: 'Store | 불란서와이너리 영등포점' / 'Cafe - 어니언' / '맛집 : 향동가'
_FIRST_LINE_SEP_RE = re.compile(r"^[^\n]{0,40}?\s*[|\-–—:]\s*(.+?)\s*$")

# `|` 분리자 뒤 텍스트(가게명 후보). 첫 줄에 `|`가 있으면 우측을 가게명 후보로.
# OG description prefix("3,380 likes, 50 comments - favkorea on October 21, 2024: \"Store | 불란서와이너리 영등포점")처럼
# 첫 줄이 길어도 `|` 뒤만 정확히 잘라낸다.
_PIPE_AFTER_RE = re.compile(r"\|\s*([^|\n\"”]+?)\s*(?=\n|$|\")")

# 📍 앞 텍스트(가게명 후보). 큐레이션 게시물에서 자주 나오는 패턴:
#   ➊삼원가든📍서울 강남구 ...
#   ① 어니언 안국 📍서울 종로구 ...
# 줄 머리의 번호·기호(➊–➓, ①–⑩, 1./2)/숫자 등)는 떼어낸다.
_PIN_BEFORE_RE = re.compile(
    r"(?:^|\n)[\s➊➋➌➍➎➏➐➑➒➓❶❷❸❹❺❻❼❽❾❿①②③④⑤⑥⑦⑧⑨⑩\-•·\d\.\)]*"
    r"([^\n📍]{2,40}?)\s*📍"
)

# 해시태그 중 후보로 쓰지 않을 일반 접미어(주제 태그, 가게명 아님).
# *카페·*맛집·*데이트는 검색하면 무관한 곳들이 무더기로 나와서 노이즈만 늘린다.
_GENERIC_TAG_SUFFIXES = (
    "맛집", "카페", "데이트", "투어", "추천", "여행", "일상", "데일리",
    "스타그램", "그램", "인스타", "스타일", "패션", "뷰티", "푸드", "음식",
    "공유", "소개", "정보", "후기", "리뷰", "기록", "셀카", "vlog", "브이로그",
    "팔로우", "팔로잉", "follow", "광고", "협찬",
)

# 인스타가 끼우는 OG description 프리픽스 제거용 패턴.
# 예: '3,380 likes, 50 comments - favkorea on October 21, 2024: "{actual caption}". '
_OG_PREFIX_RE = re.compile(
    r'^\s*[\d,]+\s*likes?[\s,]+\d[\d,]*\s*comments?\s*-\s*\S+\s+on\s+[^:]+:\s*"(.+?)"\s*\.?\s*$',
    re.DOTALL,
)

# 후보로 올리지 않을 노이즈 토큰 (전체가 이런 형태면 제외)
_NOISE_TOKENS = {
    "ad", "광고", "협찬", "파트너십", "소개", "리뷰",
}

# 행정구역 단독 토큰. 가게명 후보로 쓰면 "서울" → "서울특별시청" 같은 무관 결과 1순위가 잡힘.
_ADMIN_REGION_TOKENS = {
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "서울시", "서울특별시", "부산시", "부산광역시", "대구시", "대구광역시",
    "인천시", "인천광역시", "광주시", "광주광역시", "대전시", "대전광역시",
    "울산시", "울산광역시", "세종시", "세종특별자치시",
    "경기도", "강원도", "강원특별자치도", "충청북도", "충청남도",
    "전라북도", "전북특별자치도", "전라남도",
    "경상북도", "경상남도", "제주도", "제주특별자치도",
}

# 행정구역 접미사로 끝나는 단독 토큰. 공백 없는 단어 1개일 때만 매칭(가게명 복합어는 차단 안 됨).
# 예: "강남구", "종로구", "성수동", "홍대역", "압구정" — 차단.
#     "온누리식당", "강남노포" — 그대로 통과(다른 글자 포함).
_ADMIN_SUFFIX_RE = re.compile(r"^[가-힣]{1,8}(?:구|군|동|읍|면|역)$")


def _clean(text: str) -> str:
    text = text.strip()
    # 끝 문장부호 제거
    text = re.sub(r"[\.,!?·…\s]+$", "", text)
    # 시작 문장부호 제거
    text = re.sub(r"^[\.,!?·…\-:|\s]+", "", text)
    return text


def _is_admin_token(text: str) -> bool:
    """행정구역·지명 단독 토큰이면 True (가게명 후보로 부적합).

    단어 1개로만 구성되고 행정구역에 해당하는 경우만 차단한다.
    "온누리식당", "강남노포" 같은 복합어는 그대로 통과.
    """
    if text in _ADMIN_REGION_TOKENS:
        return True
    if _ADMIN_SUFFIX_RE.match(text):
        return True
    return False


def _is_noise(text: str) -> bool:
    if not text:
        return True
    if len(text) < 2:
        return True
    if text.lower() in _NOISE_TOKENS:
        return True
    if _is_admin_token(text):
        return True
    # 해시태그·멘션만 있는 라인
    if re.fullmatch(r"[#@]\S+(?:\s+[#@]\S+)*", text):
        return True
    return False


def _extract_pin_lines(caption: str) -> Iterable[str]:
    for m in _PIN_RE.finditer(caption):
        yield _clean(m.group(1))


def _extract_label_lines(caption: str) -> Iterable[str]:
    for m in _LABEL_RE.finditer(caption):
        yield _clean(m.group(1))


def _extract_address_patterns(caption: str) -> Iterable[str]:
    for m in _ADDRESS_RE.finditer(caption):
        yield _clean(m.group(0))


def _extract_first_line_business_name(caption: str) -> Iterable[str]:
    """캡션 첫 줄(공백·이모지 제외)에서 구분자 뒤 텍스트를 후보로."""
    first_line = next((ln for ln in caption.splitlines() if ln.strip()), "")
    if not first_line:
        return
    m = _FIRST_LINE_SEP_RE.match(first_line)
    if m:
        yield _clean(m.group(1))


def _extract_pipe_after(caption: str) -> Iterable[str]:
    """`|` 분리자 뒤 텍스트를 가게명 후보로 추출. 첫 줄에서 한 번만(노이즈 방지)."""
    first_line = next((ln for ln in caption.splitlines() if ln.strip()), "")
    if not first_line:
        return
    m = _PIPE_AFTER_RE.search(first_line)
    if m:
        yield _clean(m.group(1))


def _extract_pin_before(caption: str) -> Iterable[str]:
    """📍 마커 앞에 같은 줄로 붙은 가게명 후보를 추출한다.
    큐레이션 게시물(`➊삼원가든📍...`)에서 가게명을 놓치지 않기 위함.
    """
    for m in _PIN_BEFORE_RE.finditer(caption):
        yield _clean(m.group(1))


def _is_generic_hashtag(tag: str) -> bool:
    """해시태그가 주제·일반 태그면 True (가게명 후보로 쓰면 안 됨).

    필터 세 단계:
    1. 한글이 한 글자도 없는 태그(영문/일본어 일반어, 게임명, 트렌드 태그 등) 제외
       — 한국 가게는 거의 한글 이름이라 영문 only는 가게일 가능성 낮음.
    2. `*맛집`/`*카페` 같은 generic 접미어 매칭 제외.
    3. 행정구역 단독("서울"·"강남"·"홍대역" 등) 제외.
    """
    if not any("가" <= ch <= "힣" for ch in tag):
        return True
    if _is_admin_token(tag):
        return True
    tag_lower = tag.lower()
    return any(tag_lower.endswith(s.lower()) for s in _GENERIC_TAG_SUFFIXES)


def _extract_hashtag_candidates(hashtags: Iterable[str]) -> Iterable[str]:
    """해시태그에서 가게명 후보를 추출한다.

    - 앞의 `#` 제거
    - generic 접미어(*맛집·*카페 등) 제거
    - 너무 짧은(2자 미만) 태그 제외
    """
    for tag in hashtags or ():
        cleaned = (tag or "").lstrip("#").strip()
        if len(cleaned) < 2:
            continue
        if _is_generic_hashtag(cleaned):
            continue
        yield cleaned


def _strip_og_prefix(caption: str) -> str:
    """인스타가 끼우는 OG description 프리픽스를 제거하고 본문만 남긴다.

    매칭 실패 시 원문 그대로 반환.
    """
    m = _OG_PREFIX_RE.match(caption.strip())
    if m:
        return m.group(1)
    return caption


def extract_candidates(
    caption: str | None,
    *,
    hashtags: Iterable[str] = (),
) -> list[str]:
    """캡션·해시태그에서 장소 후보 텍스트를 추출하여 중복을 제거한 리스트로 반환한다.

    반환 순서:
      1. `|` 뒤 가게명
      2. 📍 앞 가게명 (큐레이션 게시물의 `➊가게명📍주소` 패턴)
      3. 📍 뒤 텍스트 (보통 주소)
      4. 라벨 마커 (`주소:`, `위치:` 등) 뒤
      5. 주소 패턴
      6. 첫 줄 상호명 후보
      7. 해시태그(generic 접미어 제외)

    OG description 프리픽스("X likes, Y comments - user on date: ...")는 본문만 남기고 처리.
    유의미한 후보가 없으면 빈 리스트.
    """
    if not caption and not hashtags:
        return []

    body = _strip_og_prefix(caption or "")

    seen: set[str] = set()
    out: list[str] = []

    for source in (
        _extract_pipe_after(body),
        _extract_pin_before(body),
        _extract_pin_lines(body),
        _extract_label_lines(body),
        _extract_address_patterns(body),
        _extract_first_line_business_name(body),
        _extract_hashtag_candidates(hashtags),
    ):
        for cand in source:
            if _is_noise(cand):
                continue
            key = cand.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)

    return out
