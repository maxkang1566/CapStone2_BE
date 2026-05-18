"""RQ 큐 핸들 의존성 헬퍼.

라우터에서 `request.app.state.instagram_queue`(lifespan에서 세팅)를 안전하게
꺼내 쓰기 위한 공용 헬퍼. Redis 미가동/연결 실패로 큐가 None이면 503을 던진다.

`/instagram/*`와 `/storages/{storage_id}/spots/from-naver` 등 RQ enqueue가
필요한 라우터들이 공유한다. 라우터마다 같은 패턴을 복붙하지 않기 위해 분리.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status


def get_rq_queue(request: Request):
    """앱 라이프사이클에 등록된 RQ 큐를 반환한다.

    None이면 503을 던져 호출부가 best-effort enqueue 블록에서 swallow할 수 있게 한다.
    """
    queue = getattr(request.app.state, "instagram_queue", None)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="작업 큐가 초기화되지 않았습니다 (Redis 연결을 확인해주세요).",
        )
    return queue
