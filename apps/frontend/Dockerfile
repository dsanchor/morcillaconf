# Build context: repository root (docker build --file apps/frontend/Dockerfile .).
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/
ENV UV_LINK_MODE=copy
WORKDIR /workspace/apps/frontend
COPY packages/contracts /workspace/packages/contracts
COPY apps/frontend/pyproject.toml apps/frontend/uv.lock ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY apps/frontend/src ./src
COPY apps/frontend/tests ./tests
RUN uv sync --frozen --all-groups
CMD ["/workspace/apps/frontend/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/frontend/src ./src
COPY apps/frontend/.streamlit ./.streamlit
# Precompile bytecode so cold starts don't pay for it; dependencies are
# best-effort, the app and the shared contracts must compile.
RUN uv sync --frozen --no-dev \
    && (.venv/bin/python -m compileall -q $(.venv/bin/python -c "import sysconfig as s; print(s.get_paths()['stdlib'], s.get_paths()['purelib'])") || true) \
    && .venv/bin/python -m compileall -q src /workspace/packages/contracts/src \
    && useradd --create-home --uid 10001 frontend
ENV FRONTEND_BFF_CLIENT=fake \
    FRONTEND_PORT=8501
USER frontend
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["/workspace/apps/frontend/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('FRONTEND_PORT', '8501') + '/_stcore/health', timeout=3)"]
CMD ["sh", "-c", "exec /workspace/apps/frontend/.venv/bin/streamlit run src/frontend/app.py --server.address=0.0.0.0 --server.port \"$FRONTEND_PORT\" --server.headless=true --browser.gatherUsageStats=false"]
