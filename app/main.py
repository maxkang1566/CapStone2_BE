from contextlib import asynccontextmanager
import asyncio
import sys

import anyio
from fastapi import FastAPI

from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.instagram import router as instagram_router
from app.routers.places import router as places_router
from app.routers.spots import router as spots_router
from app.routers.storages import router as storages_router
from app.routers.users import router as users_router
from app.services.playwright_manager import PlaywrightManager


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
    try:
        yield
    finally:
        # 서버 종료 시 브라우저/Playwright 자원을 정리합니다.
        await anyio.to_thread.run_sync(manager.stop)


app = FastAPI(
    title="Picklog Backend",
    description="인스타그램 장소 아카이빙 API",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(storages_router)
app.include_router(spots_router)
app.include_router(places_router)
app.include_router(instagram_router)
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