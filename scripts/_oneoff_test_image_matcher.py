"""image_place_matcher 진단/검증 도구.

목적: DB의 인스타 raw_payload 1건을 입력으로 캡션·후보·이미지 URL을 추려
`match_images_to_places`를 호출하고 분류 결과를 출력. 비용·응답시간도 같이.

DB write 없음. ANTHROPIC_API_KEY 사용 → 매 호출당 Claude 비용 발생.

사용:
    poetry run python scripts/_oneoff_test_image_matcher.py --shortcode <code> [--limit-images N]

    # shortcode 미지정 시 가장 최근 multi-place candidate가 잡힌 raw 1건 자동 선택
    poetry run python scripts/_oneoff_test_image_matcher.py
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 콘솔(cp949) 인코딩 회피
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.core.database import SessionLocal
from app.models.models import PlaceRawData
from app.services import (
    instagram_pipeline,
    naver_local_search,
    place_extractor_llm,
)
from app.services.image_place_matcher import (
    PlaceCandidateContext,
    match_images_to_places,
)
from app.services.instagram_share import _is_place_business


def _pick_raw(db, shortcode: str | None) -> PlaceRawData | None:
    q = (
        db.query(PlaceRawData)
        .filter(PlaceRawData.provider == "instagram")
        .order_by(PlaceRawData.created_at.desc())
    )
    if shortcode:
        return q.filter(PlaceRawData.external_id == shortcode).first()
    return q.first()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--shortcode", default=None, help="대상 게시물 shortcode")
    p.add_argument("--limit-images", type=int, default=None, help="이미지 개수 상한(테스트용)")
    args = p.parse_args()

    db = SessionLocal()
    try:
        raw = _pick_raw(db, args.shortcode)
        if raw is None:
            print("[error] raw_payload를 찾지 못했습니다.")
            return

        payload = raw.raw_payload or {}
        caption = payload.get("caption") or ""
        images = list(payload.get("images") or [])
        if args.limit_images is not None:
            images = images[: args.limit_images]

        print(f"[target] shortcode={raw.external_id}")
        print(f"[caption] {caption[:200]}...")
        print(f"[images] {len(images)}장")

        if len(images) < 2:
            print("[skip] 이미지 < 2 — 분류 불필요")
            return

        # 후보 추출(LLM)
        queries = place_extractor_llm.extract_places(caption, hashtags=payload.get("hashtags") or [])
        if not queries:
            print("[error] LLM이 후보 query를 못 뽑음")
            return
        print(f"[queries] {queries}")

        # 네이버 1순위 채택
        candidates: list[PlaceCandidateContext] = []
        for q in queries:
            results = naver_local_search.search_places(q)
            place_results = [r for r in results if _is_place_business(r)]
            if not place_results:
                continue
            item = place_results[0]
            candidates.append(PlaceCandidateContext(
                name=item.name,
                category=item.category_group or item.category,
            ))
        # 중복 제거(name 기준)
        seen: set[str] = set()
        deduped: list[PlaceCandidateContext] = []
        for c in candidates:
            if c.name in seen:
                continue
            seen.add(c.name)
            deduped.append(c)
        candidates = deduped

        if len(candidates) < 2:
            print(f"[skip] 후보 < 2 (n={len(candidates)}) — 분류 불필요")
            return

        print(f"[candidates] {[c.name for c in candidates]}")

        # matcher 호출
        t0 = time.perf_counter()
        result = match_images_to_places(
            caption=caption,
            candidates=candidates,
            image_urls=images,
        )
        elapsed = time.perf_counter() - t0

        print(f"\n[matcher result] {elapsed:.2f}s")
        for place_index, image_indices in sorted(result.items()):
            name = candidates[place_index].name if place_index < len(candidates) else f"<oob:{place_index}>"
            print(f"  place[{place_index}] {name}: image_indices={image_indices}")

        # 비용 추산(Haiku 4.5 단가, 1k input=$0.001, 1k output=$0.005 가정)
        # 1이미지 512px ≈ 350tokens, 캡션·후보 ≈ 1.5k tokens
        est_in = 350 * len(images) + 1500
        est_out = 500
        est_cost = est_in * 1e-6 + est_out * 5e-6
        print(f"\n[cost-estimate] in≈{est_in}tok out≈{est_out}tok → ~${est_cost:.4f}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
