"""RQ 워커가 실행하는 인스타 크롤링 잡 함수.

워커는 별도 프로세스에서 돌기 때문에 다음 자원을 새로 만든다:
- DB 세션 (SessionLocal)
- 필요 시 Playwright (OG fallback 시에만)

워커 프로세스는 동시성이 낮으므로 PlaywrightManager는 잡 단위로 lazy 초기화한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.database import SessionLocal
from app.models.models import InstagramCrawlJob, User
from app.services import instagram_pipeline, instagram_share
from app.services.naver_local_search import NaverLocalSearchError
from app.services.playwright_manager import PlaywrightManager
from app.services.spot_creator import (
    DuplicateInstagramUrlError,
    SpotCreationError,
    StorageNotFoundError,
    StoragePermissionError,
)

logger = logging.getLogger(__name__)

# 워커 프로세스 안에서 1회만 부팅되는 Playwright 매니저(OG fallback 전용).
_playwright_manager: Optional[PlaywrightManager] = None


def _get_playwright_manager() -> PlaywrightManager:
    global _playwright_manager
    if _playwright_manager is None:
        manager = PlaywrightManager()
        manager.start()
        _playwright_manager = manager
    return _playwright_manager


def process_crawl_job(job_id: str) -> None:
    """RQ 큐에서 호출되는 잡 본체. 잡 행을 갱신한다."""
    db = SessionLocal()
    try:
        job = db.query(InstagramCrawlJob).filter(InstagramCrawlJob.id == job_id).first()
        if not job:
            logger.error("crawl job not found: %s", job_id)
            return

        try:
            response, source = instagram_pipeline.fetch_post(
                job.url,
                db,
                playwright_manager=_get_playwright_manager(),
            )
            job.status = "done"
            # source 값을 캐시 hit / 직접 호출 구분 없이 저장 시점 표기로 정규화
            job.source = "apify" if source.endswith("apify") else "og_fallback"
            job.shortcode = instagram_pipeline.extract_shortcode(job.url)
            job.payload = response.model_dump(mode="json")
            job.error = None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:  # noqa: BLE001 — 워커는 모든 예외를 잡 결과로 기록
            logger.exception("crawl job failed: %s", job_id)
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def process_share_job(job_id: str) -> None:
    """`/instagram/share`의 캐시 miss 잡 본체.

    잡 행에서 url/user_id/storage_id를 읽어 share_post를 실행하고, 결과를 직렬화해
    payload에 기록한다. 도메인 예외(저장소 권한·중복 등)도 status='failed' + error로 보고.
    NaverLocalSearchError도 failed로 처리해 클라이언트가 폴링 시 명확한 에러를 받게 한다.
    """
    db = SessionLocal()
    try:
        job = db.query(InstagramCrawlJob).filter(InstagramCrawlJob.id == job_id).first()
        if not job:
            logger.error("share job not found: %s", job_id)
            return

        if job.user_id is None or job.storage_id is None:
            logger.error("share job %s missing user_id/storage_id", job_id)
            job.status = "failed"
            job.error = "잡에 사용자/저장소 컨텍스트가 누락됐습니다."
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        user = db.query(User).filter(User.id == job.user_id).first()
        if user is None:
            job.status = "failed"
            job.error = "잡 등록자가 더 이상 존재하지 않습니다."
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        try:
            result = instagram_share.share_post(
                job.url,
                job.storage_id,
                user,
                db,
                playwright_manager=_get_playwright_manager(),
            )
            response = instagram_share.share_result_to_response(result)

            job.status = "done"
            # share 잡의 source 표기는 fetch 단계 출처를 그대로 사용
            if result.crawl_source:
                job.source = "apify" if result.crawl_source.endswith("apify") else "og_fallback"
            job.shortcode = instagram_pipeline.extract_shortcode(job.url)
            job.payload = response.model_dump(mode="json")
            job.error = None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        except (
            StorageNotFoundError,
            StoragePermissionError,
            DuplicateInstagramUrlError,
            SpotCreationError,
            NaverLocalSearchError,
            instagram_pipeline.PipelineError,
        ) as e:
            logger.warning("share job %s domain failure: %s", job_id, e)
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as e:  # noqa: BLE001 — 예상 못한 모든 예외도 잡 결과로 기록
            logger.exception("share job failed: %s", job_id)
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
