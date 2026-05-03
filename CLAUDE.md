# 프로젝트 개요

- **Project Name:** Picklog
- **Core Concept:** 인스타그램 게시물을 우리 앱으로 공유하면, 해당 게시물에서 장소 정보를 추출하고 자동으로 아카이빙하는 백엔드 서비스입니다.
- **Key Feature:** 장소의 특징(분위기, 혼잡도 등)을 분석하여 MBTI 형태의 독자적인 지표로 판단하고 부여하는 기능을 포함합니다.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 언어 지침

모든 결과값, 설명, 응답은 반드시 한글로 작성한다.

## Workflow & Progress Tracking

- **Always read `claude-progress.txt`** at the beginning of every session or task to understand the current project state.
- **Update `claude-progress.txt`** before finishing a task:
  - Move completed tasks to the 'Completed' section.
  - Detail any new issues or bugs discovered.
  - Define the next immediate steps for the project.
- Ensure the progress log is concise and reflects the latest changes in the codebase.

- 모든 작업에 대해 notes/ 디렉토리에 메모를 남겨줘
- 메모에는 : 작업 내용, 결정 이유, 배운 점을 포함
- 중요한 결정의 이유(WHY)를 반드시 기록

## Commands

```bash
# Install dependencies
poetry install

# Start infrastructure (PostgreSQL + Redis)
docker-compose up

# Run development server
poetry run python run_dev.py

# Install Playwright browsers (required on first setup)
playwright install chromium
```

No test or lint configuration exists yet. Tests belong in a `/tests` directory with pytest; linting can be added via ruff.

## Tech Stack

- **Framework:** FastAPI (async) + Uvicorn ASGI server
- **ORM:** SQLAlchemy 2.x with GeoAlchemy2 (PostGIS geospatial extension)
- **Database:** PostgreSQL 15 with PostGIS 3.3 (via Docker)
- **Cache:** Redis (via Docker, not yet wired into application code)
- **Web Scraping:** Playwright (async) + BeautifulSoup4
- **Config:** python-dotenv; requires a `.env` file (see `.env.example`)
- **Python:** >=3.14, dependency management via Poetry

## Architecture

The app is a backend for "Picklog," an Instagram location archiving service. It scrapes Instagram post metadata via a headless browser and is designed to store results in a PostGIS-enabled PostgreSQL database.

### Layer Structure

```
app/
├── main.py              # App entry point: lifespan, router registration
├── core/database.py     # SQLAlchemy engine + session factory + get_db() dependency
├── routers/             # HTTP layer — thin handlers, delegate to services
│   ├── instagram.py     # POST /instagram/crawl
│   └── health.py        # GET /health/db
├── schemas/instagram.py # Pydantic request/response models
├── services/
│   ├── instagram_crawler.py   # Core scraping logic (Playwright + BeautifulSoup)
│   └── playwright_manager.py  # Browser singleton lifecycle
└── models/              # SQLAlchemy ORM models (currently empty — not yet defined)
```

### Key Design Patterns

- **Lifespan context manager** (`app/main.py`): Playwright browser is started once on server startup and shared for the entire process lifetime, then cleanly shut down.
- **Dependency injection via `Depends`**: `PlaywrightManager` and `get_db` (SQLAlchemy session) are injected into route handlers — avoid instantiating these directly inside handlers.
- **Full async**: All route handlers, crawler methods, and database interactions use `async/await`.

### Request Flow

`POST /instagram/crawl` → router injects `PlaywrightManager` via `Depends` → `InstagramCrawler` opens a browser context, navigates to the URL, waits for network idle → BeautifulSoup parses OpenGraph meta tags → returns structured JSON (URL, caption, images, location hints, OG metadata).

### Environment Variables

| Variable       | Description                                                                            |
| -------------- | -------------------------------------------------------------------------------------- |
| `DATABASE_URL` | PostgreSQL connection string, e.g. `postgresql://user:password@localhost:5432/picklog` |
