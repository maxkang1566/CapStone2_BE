"""AI팀 공간 DNA 분석 API dry-run.

§0: 본 구현 전 응답 스키마 확정용.
- mbti_axes 3키 셋(color/density/form) 일치 확인 (2026-05-14 AI 동결)
- top_tags 배열 dict의 정확한 키 이름(name vs tag, score vs weight)
- 응답 시간(콜드 스타트 vs warm)
- 호출 전/후 place_space_dna SELECT → AI가 자체 DB write 하는지 확인

--multi 플래그로 /analyze/multi 엔드포인트(다중 이미지 배열) dry-run 가능.
다중 이미지 작업의 검증용으로 다음 두 케이스도 별도 실행 권장:
  1) 1장만 있는 Place로 --multi → image_urls=[url] 단일 원소 배열 처리 확인
  2) 8장 이상 있는 Place로 --multi → 응답 시간 < 180s 확인

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


def _pick_place_single(db, place_id: int | None) -> tuple[int, str]:
    """대상 place_id와 image_url 1개 반환. /analyze/place(단일) 호출용."""
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


def _pick_place_multi(db, place_id: int | None, limit: int) -> tuple[int, list[str]]:
    """대상 place_id와 image_url 리스트 반환. /analyze/multi(다중) 호출용.

    place_id 미지정이면 PlaceImage가 가장 많은 Place 자동 선택.
    """
    if place_id is None:
        from sqlalchemy import func as sa_func
        row = (
            db.query(PlaceImage.place_id, sa_func.count(PlaceImage.id).label("cnt"))
            .group_by(PlaceImage.place_id)
            .order_by(sa_func.count(PlaceImage.id).desc(), PlaceImage.place_id.asc())
            .first()
        )
        if row is None:
            raise SystemExit("PlaceImage가 있는 Place가 없음")
        place_id = int(row[0])

    rows = (
        db.query(PlaceImage.image_url)
        .filter(PlaceImage.place_id == place_id)
        .order_by(PlaceImage.is_representative.desc(), PlaceImage.created_at.asc())
        .limit(limit)
        .all()
    )
    if not rows:
        raise SystemExit(f"place_id={place_id}의 PlaceImage 없음")
    return place_id, [r[0] for r in rows]


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
    p.add_argument(
        "--multi",
        action="store_true",
        help="/analyze/multi 엔드포인트로 다중 이미지 호출",
    )
    p.add_argument(
        "--max-images",
        type=int,
        default=10,
        help="--multi 사용 시 보낼 이미지 최대 개수 (기본 10)",
    )
    args = p.parse_args()

    db = SessionLocal()
    try:
        if args.multi:
            place_id, image_urls = _pick_place_multi(db, args.place_id, args.max_images)
            print(f"target: place_id={place_id}")
            print(f"image_urls ({len(image_urls)}):")
            for i, u in enumerate(image_urls, 1):
                print(f"  [{i}] {u}")
        else:
            place_id, image_url = _pick_place_single(db, args.place_id)
            print(f"target: place_id={place_id}")
            print(f"image_url={image_url}")

        before = _read_dna_row(db, place_id)
        print(f"\n[BEFORE] place_space_dna: {json.dumps(before, ensure_ascii=False, default=str)}")
    finally:
        db.close()

    if args.multi:
        endpoint = "/analyze/multi"
        payload = {"place_id": place_id, "image_urls": image_urls}
    else:
        endpoint = "/analyze/place"
        payload = {"place_id": place_id, "image_url": image_url}

    print(f"\nPOST {API_URL}{endpoint}")
    t0 = time.perf_counter()
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(f"{API_URL}{endpoint}", json=payload)
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
    # 2026-05-14 AI 동결: 3축(color/density/form). 그 이전 5키 셋(busy_calm 등)은 outdated.
    required = {"color", "density", "form"}
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
