FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM docker.io/library/python:3.14-slim-trixie AS builder
ARG TARGETARCH
RUN test "${TARGETARCH}" = "amd64"
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --no-cache

FROM builder AS validation
COPY tests ./tests
COPY tools ./tools
RUN uv sync --locked --no-editable --no-cache \
    && uv run --no-sync ruff check src tests tools \
    && uv run --no-sync ruff format --check src tests tools \
    && uv run --no-sync ty check src tests tools \
    && uv run --no-sync pytest

FROM docker.io/library/python:3.14-slim-trixie AS runtime
LABEL org.opencontainers.image.title="tgdoctor" \
      org.opencontainers.image.description="Polish registry search Telegram bot; linux/amd64 only" \
      org.opencontainers.image.version="0.1.0"
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data HTTP_HOST=0.0.0.0 HTTP_PORT=3000 \
    TGDOCTOR_CONTAINER=1 ENVIRONMENT=production
RUN groupadd --gid 1000 bot && useradd --uid 1000 --gid 1000 --no-create-home bot \
    && install -d -o 1000 -g 1000 /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
USER 1000:1000
EXPOSE 3000/tcp
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["/app/.venv/bin/tgdoctor", "healthcheck"]
ENTRYPOINT ["/app/.venv/bin/tgdoctor"]
CMD ["run"]
