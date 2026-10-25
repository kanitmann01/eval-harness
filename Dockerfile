# syntax=docker/dockerfile:1
#
# Two stages: the compiler and headers exist only in the builder, and the
# runtime image carries the virtualenv and the application. psycopg[binary]
# ships manylinux wheels, so build-essential is insurance for a future
# dependency without one, not a need of today's lockfile.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install ".[postgres]"

FROM python:3.12-slim AS runtime

# The commit this image was built from, so a recorded run can be tied to a
# revision. Compose and CI pass it; the default says "unknown".
ARG EVALH_HARNESS_SHA=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH" \
    EVALH_HOME=/data \
    EVALH_DATABASE_URL=sqlite:////data/db/harness.db \
    EVALH_ARTIFACTS_DIR=/data/artifacts \
    EVALH_SPECS_DIR=/data/specs

ENV EVALH_HARNESS_SHA=$EVALH_HARNESS_SHA

RUN groupadd --system --gid 1001 evalh \
    && useradd --system --uid 1001 --gid evalh --home-dir /data --no-create-home evalh

COPY --from=builder /venv /venv

WORKDIR /app
COPY examples/specs ./examples/specs
COPY scripts ./scripts

# A named volume mounted at /data is initialised from this directory on first
# use, so a fresh compose project can launch the bundled no-credential specs
# from the console. A later image does not overwrite an existing volume.
RUN install -d -o evalh -g evalh /data/artifacts /data/db /data/specs \
    && cp examples/specs/smoke.yaml examples/specs/stub-pack.yaml /data/specs/ \
    && chown -R evalh:evalh /data

USER evalh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

# The CLI opens the vault before serving, so the first start without
# EVALH_MASTER_KEY prints a key once and exits 2 without touching the database.
ENTRYPOINT ["evalh", "serve", "--host", "0.0.0.0", "--port", "8000"]
