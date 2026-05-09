"""[일회성] needs_selection dump 분석 — 정답 1순위 분석용.

dump 파일은 append-only라 1차 dry-run의 22건 + 2차 dry-run의 18건이 이어 붙어 있다.
2차분(가장 마지막 18건)만 분석한다.
"""
import json
import re

with open("seeds/seed_run_2026-05-09_pending.jsonl", encoding="utf-8") as f:
    all_rows = [json.loads(l) for l in f]
# 마지막 18건만 (3차 dry-run 분)
rows = all_rows[-18:]

print(f"총 needs_selection: {len(rows)}건\n")

print("=" * 100)
print("2차 dry-run needs_selection 18건 — 후보 전체 + 캡션 첫 부분")
print("=" * 100)

for i, r in enumerate(rows, 1):
    cap = (r.get("crawl_data") or {}).get("caption") or ""
    cap_first = re.sub(r"\s+", " ", cap[:160])
    sc = r["url"].split("?")[0].rstrip("/").split("/")[-1]
    candidates = r["candidates"]
    print(f"\n[#{i}] cands={len(candidates)}  {sc}")
    print(f"  caption[:160] = {cap_first}")
    print(f"  -- candidates (전부) --")
    for j, c in enumerate(candidates, 1):
        name = c.get("name") or "?"
        addr = c.get("road_address") or c.get("address") or ""
        cat = c.get("category_group") or c.get("category") or ""
        print(f"    {j:2}. {name:35} [{cat}] {addr}")
