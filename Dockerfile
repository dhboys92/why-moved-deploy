# 왜움직여? MCP 서버 — 카카오클라우드 VM 배포용
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# 의존성 레이어 분리 (소스 변경 시 재설치 방지)
# BuildKit 전용 --mount=type=cache는 PlayMCP in KC 빌더(kaniko 계열)가 지원하지 않아 쓰지 않는다
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm

RUN groupadd -r app && useradd -r -g app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    CACHE_DB_PATH=/data/cache.db \
    DART_API_KEY=9d3a602edd263c64ad5b84cff886a167fdc52df0

# 캐시 DB 볼륨 (컨테이너 재시작에도 DART 호출 한도 절약 유지)
RUN mkdir -p /data && chown app:app /data
VOLUME /data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["why-moved"]
