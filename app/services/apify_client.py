"""Apify Instagram 스크래퍼 액터 호출 클라이언트.

Apify의 `apify/instagram-post-scraper` 액터를 동기 호출(`call`)로 실행하고
첫 번째 dataset 아이템을 그대로 반환한다.

환경변수:
- APIFY_TOKEN: Apify API 토큰 (필수)
- APIFY_INSTAGRAM_ACTOR_ID: 사용할 액터 ID (기본값: apify/instagram-scraper)
  - 개별 게시물 URL을 directUrls로 받는 일반 스크레이퍼.
  - apify/instagram-post-scraper는 username 기반이라 사용하지 않는다.
"""
from __future__ import annotations

import os

from apify_client import ApifyClient


class ApifyError(Exception):
    """Apify 호출 관련 일반 예외."""


class ApifyConfigError(ApifyError):
    """토큰 미설정 등 설정 오류."""


class ApifyTimeoutError(ApifyError):
    """액터 실행 타임아웃."""


class ApifyEmptyResultError(ApifyError):
    """액터는 성공했지만 결과가 비어있음 (게시물 비공개/삭제 가능성)."""


def run_instagram_post_scraper(url: str, *, timeout_seconds: int = 90) -> dict:
    """Apify Instagram 게시물 스크래퍼를 동기 호출하고 첫 결과를 반환한다.

    Args:
        url: 인스타그램 게시물 URL
        timeout_seconds: 액터 실행 타임아웃(초)

    Returns:
        Apify dataset의 첫 번째 아이템(dict)

    Raises:
        ApifyConfigError: APIFY_TOKEN 미설정
        ApifyTimeoutError: 액터 실행 시간 초과
        ApifyEmptyResultError: 결과가 비어있음
        ApifyError: 그 외 호출 실패
    """
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise ApifyConfigError("APIFY_TOKEN 환경변수가 설정되어 있지 않습니다.")

    actor_id = os.getenv("APIFY_INSTAGRAM_ACTOR_ID", "apify/instagram-scraper")

    # apify/instagram-scraper는 개별 post URL을 directUrls로 받고
    # resultsType=posts(기본)면 캡션·이미지·위치·해시태그 등 게시물 상세를 반환한다.
    run_input = {
        "directUrls": [url],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
    }

    client = ApifyClient(token)

    try:
        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout_seconds)
    except Exception as e:
        # apify-client는 다양한 예외(httpx 계열, ApifyApiError 등)를 던질 수 있어 한 번에 잡는다.
        msg = str(e).lower()
        if "timeout" in msg or "timed out" in msg:
            raise ApifyTimeoutError(f"Apify 액터 실행 타임아웃: {e}") from e
        raise ApifyError(f"Apify 액터 호출 실패: {e}") from e

    if run is None:
        raise ApifyError("Apify 액터 응답이 비어있습니다.")

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        raise ApifyError("Apify 액터 응답에 datasetId가 없습니다.")

    items = list(client.dataset(dataset_id).iterate_items())
    if not items:
        raise ApifyEmptyResultError("Apify 액터 결과가 비어있습니다 (비공개/삭제 게시물 가능성).")

    return items[0]
