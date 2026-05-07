"""RQ 워커 엔트리포인트.

실행:
    poetry run python -m app.worker

Windows에서는 fork()가 없어서 SimpleWorker를 사용한다.
Linux/Mac은 기본 Worker도 동작하지만 일관성을 위해 SimpleWorker로 통일한다.
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from redis import Redis
from rq import Queue, SimpleWorker

# Playwright subprocess가 필요한 경우(Proactor) Windows에서 이벤트 루프 설정
if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("RQ_QUEUE_NAME", "instagram")

    conn = Redis.from_url(redis_url)
    queue = Queue(queue_name, connection=conn)
    worker = SimpleWorker([queue], connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
