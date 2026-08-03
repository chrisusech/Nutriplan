# NutriPlan — imagen de producción (Fly.io / cualquier host Docker)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config
COPY data ./data
COPY prompts ./prompts
COPY migrations ./migrations
COPY alembic.ini ./

RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 nutri \
    && chown -R nutri:nutri /app

USER nutri

EXPOSE 8000
# HEALTHCHECK lo define fly.toml; curl queda por si se usa otro orquestador.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "nutriplan.ui.web.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
