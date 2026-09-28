FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/
WORKDIR /workspace/apps/frontend
COPY packages/contracts /workspace/packages/contracts
COPY apps/frontend/pyproject.toml apps/frontend/uv.lock ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY apps/frontend/src ./src
COPY apps/frontend/tests ./tests
CMD ["/workspace/apps/frontend/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/frontend/src ./src
COPY apps/frontend/.streamlit ./.streamlit
ENV FRONTEND_PORT=8501
EXPOSE 8501
CMD ["sh", "-c", "exec /workspace/apps/frontend/.venv/bin/streamlit run src/frontend/app.py --server.address=0.0.0.0 --server.port \"$FRONTEND_PORT\" --server.headless=true"]
