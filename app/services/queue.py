"""워커/스크립트에서 RQ 큐 핸들을 얻는 헬퍼.

라우터(`app/routers/instagram.py`)는 라이프사이클에서 만든 큐를
`request.app.state.instagram_queue`로 주입받지만, 워커 잡 함수는 그
컨텍스트를 못 본다. 이 헬퍼는 환경변수만으로 큐를 새로 만들어 잡 함수
안에서도 enqueue할 수 있게 한다.
"""
from __future__ import annotations

import os

from redis import Redis
from rq import Queue


def get_default_queue() -> Queue:
    """REDIS_URL/RQ_QUEUE_NAME 환경변수로 새 RQ Queue를 만든다.

    호출마다 새 Redis 연결을 만든다. 빈도가 낮은 enqueue용이라 풀링은 불필요.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("RQ_QUEUE_NAME", "instagram")
    conn = Redis.from_url(redis_url)
    return Queue(queue_name, connection=conn)
