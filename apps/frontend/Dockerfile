# Build from the repository root so the relative path dependency on
# packages/contracts resolves:
#   docker build -f apps/frontend/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.13

# Keep the repository layout: apps/frontend depends on ../../packages/contracts.
WORKDIR /app/apps/frontend

# Dependencies first, in a layer that only changes with the lockfile or the
# shared contracts. The contracts package is a path dependency, so uv installs
# it here (editable) even with --no-install-project.
COPY packages/contracts /app/packages/contracts
COPY apps/frontend/pyproject.toml apps/frontend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY apps/frontend/.streamlit ./.streamlit
COPY apps/frontend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Precompile bytecode so cold starts don't pay for it (same approach as
# agents/restaurant). Dependencies are best-effort; application code and the
# shared contracts are compiled strictly so syntax errors fail the build.
RUN PYTHONDONTWRITEBYTECODE= .venv/bin/python -m compileall -q $(.venv/bin/python -c "import sysconfig as s; print(s.get_paths()['stdlib'], s.get_paths()['purelib'])") || true; \
    PYTHONDONTWRITEBYTECODE= .venv/bin/python -m compileall -q src /app/packages/contracts/src

RUN useradd --system --uid 10001 --user-group --create-home --home-dir /home/app app

USER app

ENV PATH="/app/apps/frontend/.venv/bin:${PATH}" \
    FRONTEND_PORT=8501 \
    FRONTEND_BFF_CLIENT=fake

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/_stcore/health' % os.environ.get('FRONTEND_PORT', '8501'), timeout=4)"]

# Shell form so FRONTEND_PORT is expanded at start; exec keeps Streamlit as PID 1.
CMD ["sh", "-c", "exec streamlit run src/frontend/app.py --server.port \"${FRONTEND_PORT:-8501}\" --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false"]
