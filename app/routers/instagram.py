import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
import anyio
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import (
    InstagramCrawlJob,
    Storage,
    StorageMember,
    User,
)
from app.schemas.instagram import (
    InstagramCrawlJobEnqueueResponse,
    InstagramCrawlRequest,
    InstagramCrawlResponse,
    InstagramJobStatusResponse,
    InstagramSaveRequest,
    InstagramSaveResponse,
    InstagramShareEnqueueResponse,
    InstagramShareJobStatusResponse,
    InstagramShareRequest,
    InstagramShareResponse,
)
from app.services import instagram_pipeline, instagram_share
from app.services.instagram_crawler import InstagramCrawler
from app.services.naver_local_search import NaverLocalSearchError
from app.services.playwright_manager import PlaywrightManager
from app.services.spot_creator import (
    DuplicateInstagramUrlError,
    InstagramData,
    NaverPlaceData,
    SpotCreationError,
    StorageNotFoundError,
    StoragePermissionError,
    create_spot_from_naver,
)

router = APIRouter(prefix="/instagram", tags=["instagram"])


def _get_default_storage_id(user_id: int, db: Session) -> int:
    member = (
        db.query(StorageMember)
        .join(Storage, StorageMember.storage_id == Storage.id)
        .filter(
            StorageMember.user_id == user_id,
            StorageMember.role == "owner",
            Storage.deleted_at.is_(None),
        )
        .order_by(StorageMember.joined_at.asc())
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="기본 저장소를 찾을 수 없습니다.")
    return member.storage_id


def get_manager(request: Request) -> PlaywrightManager:
    manager = getattr(request.app.state, "playwright_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Playwright가 초기화되지 않았습니다.",
        )
    return manager


@router.post("/crawl", response_model=InstagramCrawlResponse)
async def crawl_instagram_post(
    body: InstagramCrawlRequest,
    manager: PlaywrightManager = Depends(get_manager),
) -> InstagramCrawlResponse:
    """인스타그램 게시물 URL을 받아 캡션/이미지 등을 크롤링합니다."""
    crawler = InstagramCrawler(manager=manager)
    try:
        result = await anyio.to_thread.run_sync(crawler.crawl_post, str(body.url))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(e)) from e

    if not result.og_title and not result.og_description and not result.images:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="게시물을 불러올 수 없습니다. 비공개 계정이거나 삭제된 게시물일 수 있습니다.",
        )
    return result


@router.post("/save", response_model=InstagramSaveResponse, status_code=201)
def save_instagram_spot(
    body: InstagramSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstagramSaveResponse:
    """크롤링 결과 + 네이버 장소 정보를 받아 Spot으로 저장합니다.
    - 클라이언트가 /crawl로 미리 얻은 캡션/이미지와 네이버 지도에서 선택한 장소를 함께 전달합니다.
    - naver_place_id 기준으로 Place를 찾거나 생성합니다.
    - 이 storage에 동일 Place의 Spot이 이미 있으면 already_saved=True를 반환합니다.
    - storage_id 미제공 시 기본 저장소에 자동 저장합니다.
    """
    storage_id = body.storage_id if body.storage_id is not None else _get_default_storage_id(current_user.id, db)

    naver = NaverPlaceData(
        naver_place_id=body.naver_place_id,
        name=body.place_name,
        address=body.place_address,
        latitude=body.latitude,
        longitude=body.longitude,
        category_group=body.category_group,
        raw_payload=body.place_raw_payload,
    )
    instagram = InstagramData(
        url=str(body.instagram_url),
        caption=body.caption,
        thumbnail_url=body.thumbnail_url,
        user_memo=body.user_memo,
        user_rating=body.user_rating,
    )
    try:
        result = create_spot_from_naver(naver, instagram, storage_id, current_user, db)
    except StorageNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except StoragePermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except DuplicateInstagramUrlError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SpotCreationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return InstagramSaveResponse(
        spot=result.spot,
        already_saved=result.already_saved,
        place_created=result.place_created,
    )


def _get_rq_queue(request: Request):
    """앱 라이프사이클에 등록된 RQ 큐를 반환한다."""
    queue = getattr(request.app.state, "instagram_queue", None)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="작업 큐가 초기화되지 않았습니다 (Redis 연결을 확인해주세요).",
        )
    return queue


@router.post("/crawl-async", response_model=InstagramCrawlJobEnqueueResponse)
def crawl_instagram_async(
    body: InstagramCrawlRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> InstagramCrawlJobEnqueueResponse:
    """비동기 인스타 크롤링 진입점.

    - 캐시 hit이면 즉시 결과 반환(잡 생성 없음)
    - miss면 InstagramCrawlJob 행을 만들고 RQ 큐에 enqueue → job_id 반환
    """
    url = str(body.url)
    shortcode = instagram_pipeline.extract_shortcode(url)
    if not shortcode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="인스타그램 게시물 URL 형식이 아닙니다.",
        )

    # 1) 캐시 hit이면 잡 생성 없이 즉시 응답
    cached = instagram_pipeline.get_cached(db, shortcode)
    if cached:
        if cached.source == "apify":
            response = instagram_pipeline._normalize_apify(url, cached.payload)
        else:
            response = instagram_pipeline._normalize_og(url, cached.payload)
        return InstagramCrawlJobEnqueueResponse(job_id=None, status="done", result=response)

    # 2) 잡 생성 + 큐 enqueue
    job_id = str(uuid.uuid4())
    db.add(InstagramCrawlJob(
        id=job_id,
        kind="crawl",
        url=url,
        shortcode=shortcode,
        status="pending",
    ))
    db.commit()

    queue = _get_rq_queue(request)
    queue.enqueue(
        "app.services.instagram_jobs.process_crawl_job",
        job_id,
        job_id=job_id,
        job_timeout=180,
    )

    return InstagramCrawlJobEnqueueResponse(job_id=job_id, status="pending", result=None)


@router.get("/jobs/{job_id}", response_model=InstagramJobStatusResponse)
def get_instagram_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> InstagramJobStatusResponse:
    """비동기 크롤링 잡의 진행 상황과 결과를 조회한다.

    kind='crawl' 잡만 응답. share 잡은 /instagram/share-jobs/{id}로 분리.
    """
    job = (
        db.query(InstagramCrawlJob)
        .filter(InstagramCrawlJob.id == job_id, InstagramCrawlJob.kind == "crawl")
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")

    result: InstagramCrawlResponse | None = None
    if job.status == "done" and job.payload:
        result = InstagramCrawlResponse.model_validate(job.payload)

    return InstagramJobStatusResponse(
        job_id=job.id,
        status=job.status,
        source=job.source,
        result=result,
        error=job.error,
    )


@router.post("/share", response_model=InstagramShareEnqueueResponse)
def share_instagram_post(
    body: InstagramShareRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstagramShareEnqueueResponse:
    """인스타 게시물 공유 진입점 (하이브리드 sync/async).

    - 캐시 hit: 즉시 share_post 실행 → status="done", result에 InstagramShareResponse
    - 캐시 miss: 잡 등록 → status="pending", job_id 반환.
      클라이언트는 GET /instagram/share-jobs/{job_id}로 폴링.

    Apify 호출(5~30초)이 필요한 경우만 비동기로 빠지므로, 같은 URL 재공유나 다른 사용자가
    이미 본 URL은 즉시 응답된다.
    """
    url = str(body.url)
    shortcode = instagram_pipeline.extract_shortcode(url)
    if not shortcode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="인스타그램 게시물 URL 형식이 아닙니다.",
        )

    storage_id = (
        body.storage_id
        if body.storage_id is not None
        else _get_default_storage_id(current_user.id, db)
    )

    # 1) 캐시 hit이면 동기 처리(외부 호출 없음, 1~2초)
    if instagram_pipeline.get_cached(db, shortcode) is not None:
        manager = getattr(request.app.state, "playwright_manager", None)
        try:
            result = instagram_share.share_post(
                url, storage_id, current_user, db, playwright_manager=manager
            )
        except StorageNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except StoragePermissionError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except DuplicateInstagramUrlError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except SpotCreationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except instagram_pipeline.PipelineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except NaverLocalSearchError as e:
            # 외부(네이버 Local Search) 의존성 실패는 빈 결과로 위장하지 않고 502로 명시 거절.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"네이버 장소 검색에 실패했습니다: {e}",
            ) from e

        response = instagram_share.share_result_to_response(result)
        return InstagramShareEnqueueResponse(job_id=None, status="done", result=response)

    # 2) 캐시 miss → 잡 등록 + 큐 enqueue
    job_id = str(uuid.uuid4())
    db.add(InstagramCrawlJob(
        id=job_id,
        kind="share",
        url=url,
        shortcode=shortcode,
        status="pending",
        user_id=current_user.id,
        storage_id=storage_id,
    ))
    db.commit()

    queue = _get_rq_queue(request)
    queue.enqueue(
        "app.services.instagram_jobs.process_share_job",
        job_id,
        job_id=job_id,
        job_timeout=180,
    )

    return InstagramShareEnqueueResponse(job_id=job_id, status="pending", result=None)


@router.get("/share-jobs/{job_id}", response_model=InstagramShareJobStatusResponse)
def get_instagram_share_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InstagramShareJobStatusResponse:
    """share 잡의 진행 상황과 결과를 조회한다.

    kind 필터로 crawl 잡과 분리. 잡 등록자(user_id)만 본인 잡을 조회 가능.
    """
    job = (
        db.query(InstagramCrawlJob)
        .filter(InstagramCrawlJob.id == job_id, InstagramCrawlJob.kind == "share")
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")
    if job.user_id is not None and job.user_id != current_user.id:
        # 다른 사용자의 잡 조회는 차단 (정보 누출 방지). 404로 응답해 잡 존재 여부도 가림.
        raise HTTPException(status_code=404, detail="잡을 찾을 수 없습니다.")

    result: InstagramShareResponse | None = None
    if job.status == "done" and job.payload:
        result = InstagramShareResponse.model_validate(job.payload)

    return InstagramShareJobStatusResponse(
        job_id=job.id,
        status=job.status,
        result=result,
        error=job.error,
    )
