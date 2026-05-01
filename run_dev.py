import asyncio
import sys

import uvicorn


def main() -> None:
    # Windows + (특히 Git Bash/MINGW) 환경에서 asyncio subprocess가 막히면
    # Playwright가 구동되지 않고(NotImplementedError) 앱 startup이 실패합니다.
    # Uvicorn이 루프를 만들기 전에 정책을 먼저 Proactor로 고정합니다.
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()

