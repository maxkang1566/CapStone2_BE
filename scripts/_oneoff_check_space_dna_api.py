"""AI팀 공간 DNA 분석 API dry-run.

§0: 본 구현 전 응답 스키마 확정용.
- mbti_axes 5키 셋(busy_calm/calm_flashy/modern_vintage/premium_value/confidence) 일치 확인
- top_tags 배열 dict의 정확한 키 이름(name vs tag, score vs weight)
- 응답 시간(콜드 스타트 vs warm)
- 호출 전/후 place_space_dna SELECT → AI가 자체 DB write 하는지 확인

운영 DB write 안 함. 응답 print만.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.core.database import SessionLocal
from app.models.models import Place, PlaceImage, PlaceSpaceDNA

API_URL = os.getenv("SPACE_DNA_API_URL", "https://hoiiiii-dna-space.hf.space").rstrip("/")


def _pick_place(db, place_id: int | None) -> tuple[int, str]:
    """대상 place_id와 image_url 반환. place_id 미지정이면 PlaceImage 있는 첫 행 자동 선택."""
    if place_id is None:
        row = (
            db.query(Place.id, PlaceImage.image_url)
            .join(PlaceImage, PlaceImage.place_id == Place.id)
            .order_by(PlaceImage.is_representative.desc(), Place.id.asc())
            .first()
        )
        if row is None:
            raise SystemExit("PlaceImage가 있는 Place가 없음")
        return int(row[0]), str(row[1])

    img = (
        db.query(PlaceImage.image_url)
        .filter(PlaceImage.place_id == place_id)
        .order_by(PlaceImage.is_representative.desc(), PlaceImage.created_at.asc())
        .first()
    )
    if img is None:
        raise SystemExit(f"place_id={place_id}의 PlaceImage 없음")
    return place_id, str(img[0])


def _read_dna_row(db, place_id: int) -> dict | None:
    row = (
        db.query(PlaceSpaceDNA.mbti_axes, PlaceSpaceDNA.ai_summary, PlaceSpaceDNA.updated_at)
        .filter(PlaceSpaceDNA.place_id == place_id)
        .first()
    )
    if row is None:
        return None
    return {
        "mbti_axes": row[0],
        "ai_summary": (row[1] or "")[:80],
        "updated_at": str(row[2]) if row[2] else None,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--place-id", type=int, default=None)
    args = p.parse_args()

    db = SessionLocal()
    try:
        place_id, image_url = _pick_place(db, args.place_id)
        print(f"target: place_id={place_id}")
        print(f"image_url={image_url}")

        before = _read_dna_row(db, place_id)
        print(f"\n[BEFORE] place_space_dna: {json.dumps(before, ensure_ascii=False, default=str)}")
    finally:
        db.close()

    print(f"\nPOST {API_URL}/analyze/place")
    t0 = time.perf_counter()
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{API_URL}/analyze/place",
            json={"place_id": place_id, "image_url": image_url},
        )
    elapsed = time.perf_counter() - t0
    print(f"  status={resp.status_code} elapsed={elapsed:.1f}s")

    if resp.status_code != 200:
        print(f"  body: {resp.text[:500]}")
        return

    data = resp.json()
    print("\n[RESPONSE]")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])

    print("\n[SCHEMA 검증]")
    mbti = data.get("mbti_axes") or {}
    print(f"  mbti_axes keys: {sorted(mbti.keys())}")
    required = {"busy_calm", "calm_flashy", "modern_vintage", "premium_value", "confidence"}
    missing = required - set(mbti.keys())
    extra = set(mbti.keys()) - required
    print(f"  missing: {sorted(missing) or '(none)'}")
    print(f"  extra:   {sorted(extra) or '(none)'}")

    tags = data.get("top_tags") or []
    print(f"\n  top_tags count: {len(tags)}")
    if tags and isinstance(tags[0], dict):
        print(f"  top_tags[0] keys: {sorted(tags[0].keys())}")
        print(f"  top_tags[0]: {json.dumps(tags[0], ensure_ascii=False)}")
        print(f"  top_tags[1]: {json.dumps(tags[1], ensure_ascii=False) if len(tags) > 1 else '(없음)'}")

    db = SessionLocal()
    try:
        after = _read_dna_row(db, place_id)
        print(f"\n[AFTER] place_space_dna: {json.dumps(after, ensure_ascii=False, default=str)}")
        if before is None and after is not None:
            print("  → AI API가 자체적으로 DB에 write함")
        elif before == after:
            print("  → AI API는 DB write 안 함(우리 백엔드가 upsert해야 함)")
        else:
            print("  → AI API가 기존 행을 갱신함")
    finally:
        db.close()


if __name__ == "__main__":
    main()
