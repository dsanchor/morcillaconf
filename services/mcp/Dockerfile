FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY src ./src
COPY layouts ./layouts
COPY tests ./tests
ENV PYTHONPATH=/app/src
CMD ["/app/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY layouts ./layouts
RUN useradd --create-home --uid 10001 mcp \
    && mkdir -p /data \
    && chown mcp /data
ENV PYTHONPATH=/app/src \
    SEATING_DATABASE_PATH=/data/seating.db
USER mcp
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "restaurant_mcp.main"]
