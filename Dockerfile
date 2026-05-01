FROM python:3.14-slim

WORKDIR /app

# Poetry 설치
RUN pip install poetry

# 의존성 파일 먼저 복사 (캐시 활용)
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root

# Playwright Chromium + 시스템 의존성 설치
RUN playwright install chromium --with-deps

# 앱 코드 복사
COPY . .

# Railway가 PORT 환경변수를 자동 주입함
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
