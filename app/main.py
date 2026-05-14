from contextlib import asynccontextmanager
import asyncio
import logging
import os
import sys

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from rq import Queue

from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.instagram import router as instagram_router
from app.routers.invitations import router as invitations_router
from app.routers.places import router as places_router
from app.routers.spots import router as spots_router
from app.routers.storages import router as storages_router
from app.routers.users import router as users_router
from app.services.playwright_manager import PlaywrightManager

logger = logging.getLogger(__name__)


# Windows에서 Playwright는 subprocess 실행이 필요해서 Proactor 이벤트 루프가 필요합니다.
# (특히 Git Bash/MINGW 환경에서 Selector 루프로 잡히면 NotImplementedError가 날 수 있음)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 수명 주기 동안 Playwright(브라우저)를 1회만 띄워 재사용합니다.
    manager = PlaywrightManager()
    await anyio.to_thread.run_sync(manager.start)
    # 라우터/의존성에서 접근할 수 있도록 app.state에 보관합니다.
    app.state.playwright_manager = manager

    # Redis + RQ 큐 초기화 (인스타 비동기 크롤링용)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("RQ_QUEUE_NAME", "instagram")
    try:
        redis_conn = Redis.from_url(redis_url)
        redis_conn.ping()
        app.state.redis = redis_conn
        app.state.instagram_queue = Queue(queue_name, connection=redis_conn)
    except Exception as e:  # noqa: BLE001 — Redis 미가동 시에도 다른 라우터는 동작해야 함
        logger.warning("Redis 연결 실패: %s — /instagram/crawl-async 사용 불가", e)
        app.state.redis = None
        app.state.instagram_queue = None

    try:
        yield
    finally:
        # 서버 종료 시 브라우저/Playwright 자원을 정리합니다.
        await anyio.to_thread.run_sync(manager.stop)
        if getattr(app.state, "redis", None) is not None:
            try:
                app.state.redis.close()
            except Exception:  # noqa: BLE001
                pass


app = FastAPI(
    title="Picklog Backend",
    description="인스타그램 장소 아카이빙 API",
    lifespan=lifespan,
)

# 프론트엔드 로컬 개발 서버에서 호출할 수 있도록 CORS 허용.
# credentials=True를 쓰려면 origin을 명시적으로 나열해야 하므로 흔한 dev 포트만 등록한다.
_dev_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(storages_router)
app.include_router(spots_router)
app.include_router(places_router)
app.include_router(instagram_router)
app.include_router(invitations_router)
app.include_router(health_router)

@app.get("/")
async def read_root():
    """
    서버가 정상적으로 작동하는지 확인하는 테스트용 API입니다.
    """
    return {
        "status": "online",
        "message": "환영합니다, 현민님!",
        "tech_stack": ["FastAPI", "PostGIS", "Redis", "SQLAlchemy"]
    }