FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim AS base

COPY --from=uv /uv /uvx /bin/

WORKDIR /workspace/agents/cashier/src/cashier_agent
COPY packages/contracts /workspace/packages/contracts
COPY agents/cashier/src/cashier_agent/pyproject.toml agents/cashier/src/cashier_agent/uv.lock agents/cashier/src/cashier_agent/uv.toml ./

FROM base AS test
RUN uv sync --frozen --all-groups --no-install-project
COPY agents/cashier/src/cashier_agent/cashier_agent ./cashier_agent
COPY agents/cashier/src/cashier_agent/tests ./tests
COPY agents/cashier/src/cashier_agent/main.py ./
CMD ["/workspace/agents/cashier/src/cashier_agent/.venv/bin/python", "-m", "pytest"]

FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY agents/cashier/src/cashier_agent/cashier_agent ./cashier_agent
COPY agents/cashier/src/cashier_agent/main.py ./
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 cashier \
    && chown -R cashier:cashier /workspace

ENV PORT=8090
USER cashier
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["/workspace/agents/cashier/src/cashier_agent/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)"]
CMD ["/workspace/agents/cashier/src/cashier_agent/.venv/bin/python", "main.py"]
