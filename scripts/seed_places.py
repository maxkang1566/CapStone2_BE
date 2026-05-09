"""운영 서버 Places 시딩 스크립트.

AI팀이 공간 DNA(MBTI 4축) 분석에 쓸 데이터를 운영 Supabase에 채워 넣는다.

두 가지 모드:
    1) `--mode share` (기본, 메인 경로)
       seeds/instagram_urls.txt 의 인스타 URL을 한 줄씩 읽어 `POST /instagram/share`로 호출.
       응답이 cache hit(동기 done)이면 즉시 분기 처리, miss(pending+job_id)이면
       `GET /instagram/share-jobs/{id}`를 폴링해 done/failed가 될 때까지 대기.

    2) `--mode naver` (폴백 경로)
       seeds/places_naver_fallback.csv 의 행을 읽어 `POST /places/from-naver` 호출.
       Spot은 만들지 않고 Place + 네이버 raw_data만 채운다 (블로그 리뷰는 백그라운드 자동 적재).

환경변수:
    BASE_URL       — 예: https://capstone2be-production.up.railway.app
    SEED_EMAIL     — 시드 계정 이메일 (예: test@example.com)
    SEED_PASSWORD  — 시드 계정 비밀번호

옵션:
    --input PATH      입력 파일 경로 override
    --mode share|naver  실행 모드 (기본: share)
    --limit N         앞에서 N개만 처리 (dry-run/표본 측정용)
    --storage-id ID   Spot 저장 storage 강제 지정 (미지정 시 시드 계정 기본 저장소)

출력:
    seeds/seed_run_<YYYY-MM-DD>.log         — 행별 실행 결과 (text)
    seeds/seed_run_<YYYY-MM-DD>_pending.jsonl — needs_selection 분기 candidates dump
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
SEEDS_DIR = ROOT / "seeds"

DEFAULT_INSTAGRAM_INPUT = SEEDS_DIR / "instagram_urls.txt"
DEFAULT_NAVER_INPUT = SEEDS_DIR / "places_naver_fallback.csv"

POLL_INTERVAL_SEC = 3.0
POLL_TIMEOUT_SEC = 90.0
INTER_CALL_SLEEP_SEC = 2.5
HTTP_TIMEOUT_SEC = 30.0
MAX_RETRY_ON_5XX = 2


logger = logging.getLogger("seed_places")


@dataclass
class RunStats:
    total: int = 0
    saved: int = 0
    already_saved: int = 0
    needs_selection: int = 0
    not_a_place_post: int = 0
    failed: int = 0
    skipped: int = 0
    naver_created: int = 0
    naver_existing: int = 0
    place_ids: list[int] = field(default_factory=list)


class SeedClient:
    """시드 계정으로 운영 API에 호출하는 클라이언트.

    토큰 만료(401) 시 자동 재로그인 후 1회 재시도.
    5xx/네트워크 타임아웃 시 지수 백오프 재시도.
    """

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self._client = httpx.Client(timeout=HTTP_TIMEOUT_SEC, base_url=self.base_url)
        self._token: str | None = None

    def close(self) -> None:
        self._client.close()

    # ---------- 인증 ----------

    def login(self) -> None:
        # OAuth2PasswordRequestForm: form-data username/password
        resp = self._client.post(
            "/auth/login",
            data={"username": self.email, "password": self.password},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"로그인 실패 ({resp.status_code}): {resp.text}")
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError(f"응답에 access_token이 없습니다: {resp.text}")
        self._token = token
        logger.info("로그인 성공: %s", self.email)

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("토큰 미발급. login()을 먼저 호출하세요.")
        return {"Authorization": f"Bearer {self._token}"}

    # ---------- 저수준 요청 (재시도/재로그인 포함) ----------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        attempt = 0
        relogged_in = False
        while True:
            attempt += 1
            try:
                resp = self._client.request(
                    method,
                    path,
                    json=json_body,
                    params=params,
                    headers=self._auth_headers(),
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.NetworkError) as e:
                if attempt <= MAX_RETRY_ON_5XX:
                    backoff = 2 ** attempt
                    logger.warning("네트워크 오류 — %s초 후 재시도 (%d): %s", backoff, attempt, e)
                    time.sleep(backoff)
                    continue
                raise

            # 401: 토큰 만료 → 재로그인 후 1회 재시도
            if resp.status_code == 401 and not relogged_in:
                logger.info("401 — 토큰 재발급")
                self.login()
                relogged_in = True
                continue

            # 5xx: 백오프 재시도
            if resp.status_code in (502, 503, 504) and attempt <= MAX_RETRY_ON_5XX:
                backoff = 2 ** attempt
                logger.warning(
                    "%s 응답 — %s초 후 재시도 (%d): %s",
                    resp.status_code, backoff, attempt, resp.text[:200],
                )
                time.sleep(backoff)
                continue

            return resp

    # ---------- 인스타 share ----------

    def share_instagram(self, url: str, storage_id: int | None) -> dict:
        body: dict[str, Any] = {"url": url}
        if storage_id is not None:
            body["storage_id"] = storage_id
        resp = self._request("POST", "/instagram/share", json_body=body)
        if resp.status_code != 200:
            raise SeedHttpError(resp.status_code, resp.text, "/instagram/share")
        return resp.json()

    def get_share_job(self, job_id: str) -> dict:
        resp = self._request("GET", f"/instagram/share-jobs/{job_id}")
        if resp.status_code != 200:
            raise SeedHttpError(resp.status_code, resp.text, f"/instagram/share-jobs/{job_id}")
        return resp.json()

    def poll_share_job(self, job_id: str) -> dict:
        """잡이 done/failed가 될 때까지 폴링한다. 타임아웃 시 RuntimeError."""
        deadline = time.monotonic() + POLL_TIMEOUT_SEC
        while True:
            payload = self.get_share_job(job_id)
            status = payload.get("status")
            if status in ("done", "failed"):
                return payload
            if time.monotonic() >= deadline:
                raise RuntimeError(f"share 잡 폴링 타임아웃 ({POLL_TIMEOUT_SEC}s): {job_id}")
            time.sleep(POLL_INTERVAL_SEC)

    # ---------- 네이버 직접 ----------

    def upsert_from_naver(self, body: dict) -> dict:
        resp = self._request("POST", "/places/from-naver", json_body=body)
        if resp.status_code not in (200, 201):
            raise SeedHttpError(resp.status_code, resp.text, "/places/from-naver")
        return resp.json()


class SeedHttpError(Exception):
    def __init__(self, status_code: int, text: str, path: str) -> None:
        super().__init__(f"{path} {status_code}: {text[:300]}")
        self.status_code = status_code
        self.text = text
        self.path = path


# ---------- 입력 파일 로딩 ----------


def load_instagram_urls(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def load_naver_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            naver_place_id = (row.get("naver_place_id") or "").strip()
            name = (row.get("name") or "").strip()
            if not naver_place_id or not name:
                logger.warning("naver_place_id/name 비어있어 스킵: %r", row)
                continue
            payload: dict[str, Any] = {
                "naver_place_id": naver_place_id,
                "name": name,
                "address": (row.get("address") or "").strip() or None,
                "category_group": (row.get("category_group") or "").strip() or None,
                "phone": (row.get("phone") or "").strip() or None,
                "homepage_url": (row.get("homepage_url") or "").strip() or None,
            }
            lat = (row.get("latitude") or "").strip()
            lng = (row.get("longitude") or "").strip()
            if lat:
                payload["latitude"] = float(lat)
            if lng:
                payload["longitude"] = float(lng)
            raw = (row.get("raw_payload") or "").strip()
            if raw:
                try:
                    payload["raw_payload"] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("raw_payload JSON 파싱 실패, 원문 string 보존: %s", raw[:80])
                    payload["raw_payload"] = {"_raw_string": raw}
            rows.append(payload)
    return rows


# ---------- 메인 흐름 ----------


def run_share_mode(
    client: SeedClient,
    urls: list[str],
    storage_id: int | None,
    pending_path: Path,
) -> RunStats:
    stats = RunStats(total=len(urls))
    pending_fp = pending_path.open("a", encoding="utf-8")
    try:
        for idx, url in enumerate(urls, start=1):
            logger.info("[%d/%d] %s", idx, len(urls), url)
            try:
                envelope = client.share_instagram(url, storage_id)
            except SeedHttpError as e:
                if e.status_code == 409:
                    logger.info("  └ 409 already_saved (다른 spot 흐름) — 스킵")
                    stats.already_saved += 1
                else:
                    logger.error("  └ /share 실패: %s", e)
                    stats.failed += 1
                _sleep_between_calls()
                continue
            except Exception as e:
                logger.exception("  └ /share 예외: %s", e)
                stats.failed += 1
                _sleep_between_calls()
                continue

            inner: dict[str, Any] | None = envelope.get("result")
            if envelope.get("status") == "pending":
                job_id = envelope["job_id"]
                logger.info("  └ pending job_id=%s, 폴링 시작", job_id)
                try:
                    job = client.poll_share_job(job_id)
                except Exception as e:
                    logger.error("  └ 폴링 실패: %s", e)
                    stats.failed += 1
                    _sleep_between_calls()
                    continue
                if job.get("status") == "failed":
                    logger.error("  └ 잡 failed: %s", job.get("error"))
                    stats.failed += 1
                    _sleep_between_calls()
                    continue
                inner = job.get("result")

            if inner is None:
                logger.error("  └ 결과 비어있음 (envelope=%s)", envelope)
                stats.failed += 1
                _sleep_between_calls()
                continue

            _process_share_result(inner, url, stats, pending_fp)
            _sleep_between_calls()
    finally:
        pending_fp.close()
    return stats


def _process_share_result(
    result: dict[str, Any], url: str, stats: RunStats, pending_fp
) -> None:
    status = result.get("status")
    if status == "saved":
        spot = result.get("spot") or {}
        place_id = spot.get("place_id")
        already = bool(result.get("already_saved"))
        if already:
            stats.already_saved += 1
            logger.info("  └ already_saved place_id=%s", place_id)
        else:
            stats.saved += 1
            logger.info(
                "  └ saved place_id=%s place_created=%s",
                place_id, result.get("place_created"),
            )
        if isinstance(place_id, int):
            stats.place_ids.append(place_id)
    elif status == "needs_selection":
        stats.needs_selection += 1
        candidates = result.get("candidates") or []
        logger.info("  └ needs_selection 후보=%d → pending dump", len(candidates))
        pending_fp.write(json.dumps(
            {"url": url, "candidates": candidates, "crawl_data": result.get("crawl_data")},
            ensure_ascii=False,
        ) + "\n")
    elif status == "not_a_place_post":
        stats.not_a_place_post += 1
        logger.info("  └ not_a_place_post — 스킵")
    else:
        stats.failed += 1
        logger.error("  └ 알 수 없는 status: %s", status)


def run_naver_mode(client: SeedClient, rows: list[dict[str, Any]]) -> RunStats:
    stats = RunStats(total=len(rows))
    for idx, body in enumerate(rows, start=1):
        logger.info("[%d/%d] %s (%s)", idx, len(rows), body["name"], body["naver_place_id"])
        try:
            resp = client.upsert_from_naver(body)
        except SeedHttpError as e:
            logger.error("  └ /places/from-naver 실패: %s", e)
            stats.failed += 1
            _sleep_between_calls()
            continue
        place_id = resp.get("place_id")
        created = bool(resp.get("created"))
        if created:
            stats.naver_created += 1
            logger.info("  └ created place_id=%s", place_id)
        else:
            stats.naver_existing += 1
            logger.info("  └ existing place_id=%s", place_id)
        if isinstance(place_id, int):
            stats.place_ids.append(place_id)
        _sleep_between_calls()
    return stats


def _sleep_between_calls() -> None:
    time.sleep(INTER_CALL_SLEEP_SEC)


# ---------- 진입점 ----------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="운영 Places 시딩 스크립트")
    parser.add_argument("--mode", choices=["share", "naver"], default="share")
    parser.add_argument("--input", type=Path, default=None, help="입력 파일 경로")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N개만 처리")
    parser.add_argument("--storage-id", type=int, default=None, help="share 모드에서 Spot 저장 storage")
    return parser.parse_args()


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"[fatal] 환경변수 {name}가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(2)
    return value


def main() -> int:
    args = parse_args()

    base_url = require_env("BASE_URL")
    email = require_env("SEED_EMAIL")
    password = require_env("SEED_PASSWORD")

    today = datetime.now().strftime("%Y-%m-%d")
    log_path = SEEDS_DIR / f"seed_run_{today}.log"
    pending_path = SEEDS_DIR / f"seed_run_{today}_pending.jsonl"

    configure_logging(log_path)
    logger.info("=== seed_places 시작: mode=%s base=%s ===", args.mode, base_url)

    client = SeedClient(base_url, email, password)
    try:
        client.login()

        if args.mode == "share":
            input_path = args.input or DEFAULT_INSTAGRAM_INPUT
            urls = load_instagram_urls(input_path)
            if args.limit:
                urls = urls[: args.limit]
            logger.info("share 입력: %s (%d건)", input_path, len(urls))
            stats = run_share_mode(client, urls, args.storage_id, pending_path)
            _print_share_summary(stats)
        else:
            input_path = args.input or DEFAULT_NAVER_INPUT
            rows = load_naver_csv(input_path)
            if args.limit:
                rows = rows[: args.limit]
            logger.info("naver 입력: %s (%d건)", input_path, len(rows))
            stats = run_naver_mode(client, rows)
            _print_naver_summary(stats)
    finally:
        client.close()

    return 0


def _print_share_summary(stats: RunStats) -> None:
    saved_ratio = (stats.saved / stats.total) if stats.total else 0.0
    logger.info(
        "=== share 요약 total=%d saved=%d already=%d needs=%d not_place=%d failed=%d "
        "(saved_ratio=%.1f%%) ===",
        stats.total, stats.saved, stats.already_saved, stats.needs_selection,
        stats.not_a_place_post, stats.failed, saved_ratio * 100,
    )
    if stats.place_ids:
        logger.info("place_ids: %s", stats.place_ids)


def _print_naver_summary(stats: RunStats) -> None:
    logger.info(
        "=== naver 요약 total=%d created=%d existing=%d failed=%d ===",
        stats.total, stats.naver_created, stats.naver_existing, stats.failed,
    )
    if stats.place_ids:
        logger.info("place_ids: %s", stats.place_ids)


if __name__ == "__main__":
    sys.exit(main())
