FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY videocatalog/ videocatalog/
RUN uv sync --frozen --no-dev
ENTRYPOINT ["uv", "run", "videocatalog"]
