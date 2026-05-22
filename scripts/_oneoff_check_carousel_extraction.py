"""[일회성] 인스타 캐러셀 raw 응답의 이미지 키 형태 진단.

목적:
  Apify 액터(`apify/instagram-scraper`)가 캐러셀 게시물을 어떤 키로 반환하는지
  운영/로컬 DB의 실제 적재된 raw_payload로 검증.
  - `images` 평탄 배열이 슬라이드 전체를 잡고 있는가?
  - `childPosts`/`sidecarChildren`/`carouselMedia` 같은 자식 키가 따로 있는가?
  - `_normalize_apify`가 누락 없이 캐러셀을 펼치는가?

사용:
  poetry run python scripts/_oneoff_check_carousel_extraction.py            # 자동 샘플 5건
  poetry run python scripts/_oneoff_check_carousel_extraction.py --limit 10
  poetry run python scripts/_oneoff_check_carousel_extraction.py --shortcode DWtRCqpkZPt

전제:
  read-only. place_raw_data만 SELECT.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 프로젝트 루트 import 가능하도록
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.core.database import SessionLocal  # noqa: E402
from app.models.models import PlaceRawData  # noqa: E402
from app.services.instagram_pipeline import _normalize_apify, _split_meta_keys  # noqa: E402


_CHILD_KEYS = ("childPosts", "sidecarChildren", "carouselMedia")


def _summarize_payload(raw_payload: dict) -> dict:
    """raw_payload에서 이미지 관련 키 모양을 요약."""
    payload, source, url = _split_meta_keys(raw_payload)

    images = payload.get("images")
    display_url = payload.get("displayUrl")
    error = payload.get("error")

    children_summary = {}
    for key in _CHILD_KEYS:
        val = payload.get(key)
        if isinstance(val, list):
            # 자식 객체의 images 길이도 같이
            sizes = []
            for child in val:
                if isinstance(child, dict):
                    ch_images = child.get("images")
                    ch_display = child.get("displayUrl")
                    if isinstance(ch_images, list):
                        sizes.append(f"images={len(ch_images)}")
                    elif ch_display:
                        sizes.append("displayUrl")
                    else:
                        sizes.append("?")
            children_summary[key] = {"count": len(val), "shapes": sizes}

    return {
        "source": source,
        "url": url,
        "error": error,
        "images_len": len(images) if isinstance(images, list) else None,
        "has_displayUrl": bool(display_url),
        "children": children_summary,
        "payload_keys": sorted(payload.keys()),
    }


def _normalize_check(payload: dict, url: str) -> int:
    """`_normalize_apify` 호출 결과의 images 길이 반환."""
    normalized = _normalize_apify(url or "https://www.instagram.com/p/_/", payload)
    return len(normalized.images)


def _dump_row(row: PlaceRawData) -> None:
    summary = _summarize_payload(row.raw_payload or {})
    payload, _src, _url = _split_meta_keys(row.raw_payload or {})
    normalized_len = _normalize_check(payload, _url or "")

    print(f"[shortcode={row.provider_place_id} place_id={row.place_id}]")
    print(f"  source: {summary['source']}  collected_at: {row.collected_at:%Y-%m-%d %H:%M}")
    if summary["error"]:
        print(f"  error: {summary['error']}")
    print(f"  images(평탄): len={summary['images_len']}  displayUrl: {summary['has_displayUrl']}")
    if summary["children"]:
        for k, info in summary["children"].items():
            print(f"  {k}: count={info['count']}  shapes={info['shapes']}")
    else:
        print("  childPosts/sidecarChildren/carouselMedia: 키 없음")
    print(f"  normalize_apify → images len = {normalized_len}")
    # 평탄 길이 vs normalize 길이 비교
    if summary["images_len"] is not None and normalized_len != summary["images_len"]:
        print(
            f"  ⚠️ 차이: raw images={summary['images_len']}  vs normalized={normalized_len}"
        )
    print(f"  payload_keys: {summary['payload_keys'][:15]}{'...' if len(summary['payload_keys']) > 15 else ''}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shortcode", help="특정 shortcode만 검사", default=None)
    parser.add_argument("--limit", type=int, default=5, help="자동 샘플링 시 검사할 행 수")
    parser.add_argument(
        "--all-images",
        action="store_true",
        help="images 평탄 길이 무관 전체 조회 (기본은 len>=2 캐러셀 우선)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(PlaceRawData).filter(PlaceRawData.provider == "instagram")

        if args.shortcode:
            row = q.filter(PlaceRawData.provider_place_id == args.shortcode).first()
            if not row:
                print(f"shortcode={args.shortcode} 행 없음")
                return
            _dump_row(row)
            return

        rows = q.order_by(PlaceRawData.collected_at.desc()).limit(200).all()
        carousel_rows = []
        non_carousel_rows = []
        for r in rows:
            payload, _, _ = _split_meta_keys(r.raw_payload or {})
            imgs = payload.get("images")
            if isinstance(imgs, list) and len(imgs) >= 2:
                carousel_rows.append(r)
            else:
                non_carousel_rows.append(r)

        print(f"=== 최근 raw 200건 중 캐러셀 의심(len>=2): {len(carousel_rows)}건 ===\n")

        sample = carousel_rows[: args.limit] if not args.all_images else carousel_rows
        for row in sample:
            _dump_row(row)

        if not carousel_rows:
            print("캐러셀 의심 행이 없음. 단일 이미지 샘플 1건 출력:")
            if non_carousel_rows:
                _dump_row(non_carousel_rows[0])
    finally:
        db.close()


if __name__ == "__main__":
    main()
