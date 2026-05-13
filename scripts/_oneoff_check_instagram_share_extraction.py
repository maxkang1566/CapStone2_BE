"""인스타 /share 자동 매핑 dry-run.

목적: 다중 장소 게시물에서 어느 단계가 누락·오탐을 만드는지 진단.

흐름 재현:
1. instagram_pipeline.fetch_post → 캡션·해시태그 raw 확인 (DB 캐시 우선)
2. place_extractor.extract_candidates → 정규식 후보 출력
3. (옵션) place_extractor_llm.extract_places → LLM 후보 + 정규식 차집합 비교
4. (옵션) 각 후보별 naver_local_search.search_places → 1순위와 _is_place_business 필터 결과

DB write 없음. --with-llm/--with-naver 플래그 시에만 외부 API 호출.

사용:
    poetry run python scripts/_oneoff_check_instagram_share_extraction.py \\
        --url https://www.instagram.com/p/DWtRCqpkZPt/ [--with-llm] [--with-naver]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 기본 콘솔(cp949)에서 이모지·일부 한자 출력 시 UnicodeEncodeError 회피
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.core.database import SessionLocal
from app.services import (
    instagram_pipeline,
    naver_local_search,
    place_extractor,
    place_extractor_llm,
)
from app.services.instagram_share import _NON_PLACE_CATEGORY_GROUPS, _is_place_business


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--with-llm", action="store_true",
                   help="Claude API로 LLM 추출기 실행 (1콜)")
    p.add_argument("--with-naver", action="store_true",
                   help="네이버 Local Search 실제 호출 (각 후보당 1콜)")
    args = p.parse_args()

    print(f"[target] {args.url}")
    shortcode = instagram_pipeline.extract_shortcode(args.url)
    print(f"  shortcode={shortcode}")

    db = SessionLocal()
    try:
        # 1) fetch_post — 캐시 hit이면 외부 호출 0회. miss면 Apify 또는 OG fallback.
        #    이 스크립트는 Playwright manager를 안 넘기므로 OG fallback도 불가 (PipelineError).
        cached = instagram_pipeline.get_cached(db, shortcode) if shortcode else None
        if cached is None:
            print("\n[cache MISS] 캐시에 없음 — 캡션을 보려면 운영 /instagram/share 한 번 호출 필요")
            print("  (이 dry-run은 외부 크롤링은 안 함)")
            return

        print(f"\n[cache HIT] source={cached.source} collected_at={cached.collected_at}")
        # 캐시 payload는 source에 따라 정규화 함수가 다름
        if cached.source == "apify":
            crawl = instagram_pipeline._normalize_apify(args.url, cached.payload)
        else:
            crawl = instagram_pipeline._normalize_og(args.url, cached.payload)

        print("\n=== 캡션 (raw) ===")
        print(crawl.caption or "(없음)")
        print(f"\n=== 해시태그 ({len(crawl.hashtags or [])}건) ===")
        for h in crawl.hashtags or []:
            print(f"  #{h}")

        # 2) extract_candidates (정규식)
        candidates = place_extractor.extract_candidates(
            crawl.caption,
            hashtags=crawl.hashtags,
        )
        print(f"\n=== 정규식 extract_candidates 결과 ({len(candidates)}건) ===")
        for i, c in enumerate(candidates, 1):
            print(f"  {i:>2}. {c!r}")

        # 3) LLM 추출기 (옵션)
        llm_candidates: list[str] | None = None
        if args.with_llm:
            llm_candidates = place_extractor_llm.extract_places(
                crawl.caption,
                hashtags=crawl.hashtags,
            )
            print("\n=== LLM extract_places 결과 ===")
            if llm_candidates is None:
                print("  (LLM 비활성/실패 → 운영에선 정규식 폴백)")
            else:
                print(f"  총 {len(llm_candidates)}건:")
                for i, c in enumerate(llm_candidates, 1):
                    print(f"  {i:>2}. {c!r}")

                # 정규식 vs LLM 차집합 비교
                regex_set = set(candidates)
                llm_set = set(llm_candidates)
                print("\n=== diff (정규식 ▶ LLM) ===")
                print(f"  정규식만 ({len(regex_set - llm_set)}): "
                      f"{sorted(regex_set - llm_set)}")
                print(f"  LLM만    ({len(llm_set - regex_set)}): "
                      f"{sorted(llm_set - regex_set)}")
                print(f"  교집합   ({len(regex_set & llm_set)}): "
                      f"{sorted(regex_set & llm_set)}")

        if not args.with_naver:
            print("\n(네이버 검색은 --with-naver 플래그로 활성화)")
            return

        # 4) 각 후보로 네이버 Local Search → 1순위 + 필터 결과
        # --with-llm + --with-naver면 LLM 결과를, 아니면 정규식 결과를 검색
        search_targets = llm_candidates if llm_candidates is not None else candidates
        target_label = "LLM" if llm_candidates is not None else "정규식"
        print(f"\n=== 네이버 Local Search 1순위 + 필터 ({target_label} 후보 기반) ===")
        print(f"  blacklist: {sorted(_NON_PLACE_CATEGORY_GROUPS)}")
        print()
        for i, query in enumerate(search_targets, 1):
            print(f"[{i:>2}] query={query!r}")
            try:
                results = naver_local_search.search_places(query)
            except naver_local_search.NaverLocalSearchError as e:
                print(f"      ERROR: {e}")
                continue
            if not results:
                print("      (네이버 0건)")
                continue
            for j, r in enumerate(results, 1):
                passes = _is_place_business(r)
                mark = "OK" if passes else "BLOCK"
                tag = " ←1순위" if j == 1 else ""
                print(f"      {mark:>5} [{j}] {r.name!r} category_group={r.category_group!r}{tag}")
                if j == 1 and not passes:
                    print("      → 1순위가 차단되어 이 query는 후보에서 제외됨")
                if j == 1 and passes:
                    print(f"      → naver_place_id={r.naver_place_id}")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
