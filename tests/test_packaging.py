"""Packaging wiring: compose topology, image shape, dockerignore and env example.

The compose assertions parse the YAML and check the references between services
and named volumes rather than matching the file as text. The smoke script has
its own HTTP-seam test file because it drives a real server.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "compose.yaml"
DOCKERFILE = REPO / "Dockerfile"
ENV_EXAMPLE = REPO / ".env.example"
DOCKERIGNORE = REPO / ".dockerignore"


def test_compose_wires_the_harness_to_a_healthy_named_volume_database() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"db", "harness"}
    db = services["db"]
    harness = services["harness"]

    assert db["image"] == "postgres:16"
    assert any("pg_isready" in part for part in db["healthcheck"]["test"])
    assert harness["depends_on"]["db"]["condition"] == "service_healthy"

    assert set(compose["volumes"]) == {"postgres-data", "harness-data"}
    assert db["volumes"] == ["postgres-data:/var/lib/postgresql/data"]
    assert harness["volumes"] == ["harness-data:/data"]

    environment = harness["environment"]
    assert environment["EVALH_DATABASE_URL"].startswith("postgresql+psycopg://")
    assert "@db:5432/" in environment["EVALH_DATABASE_URL"]
    assert environment["EVALH_HOME"] == "/data"
    assert environment["EVALH_ARTIFACTS_DIR"] == "/data/artifacts"
    assert environment["EVALH_SPECS_DIR"] == "/data/specs"
    assert harness["ports"] == ["${EVALH_PORT:-8000}:8000"]
    # The first start without a key must exit 2 and stay exited; a restart
    # policy would print a fresh key in a loop instead of waiting for the
    # operator to put the printed key in .env.
    assert "restart" not in harness
    assert "${EVALH_POSTGRES_PASSWORD:?" in db["environment"]["POSTGRES_PASSWORD"]
    assert "${EVALH_MASTER_KEY:-}" in environment["EVALH_MASTER_KEY"]


def test_dockerfile_is_multistage_slim_nonroot_and_health_checked() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert stages[0].endswith("AS builder")
    assert stages[-1] == "FROM python:3.12-slim AS runtime"
    runtime = text.split("AS runtime", 1)[1]

    assert "build-essential" not in runtime
    assert "gcc" not in runtime
    assert "USER evalh" in runtime
    assert "USER root" not in runtime
    assert "EXPOSE 8000" in runtime
    assert "HEALTHCHECK" in runtime
    assert "/healthz" in runtime
    assert "EVALH_HOME=/data" in runtime
    assert (
        'ENTRYPOINT ["evalh", "serve", "--host", "0.0.0.0", "--port", "8000"]'
        in runtime
    )
    assert 'pip install ".[postgres]"' in text
    assert "[dev]" not in text
    # The named /data volume is primed with the offline spec the smoke script
    # launches, so a fresh compose project can run it without a bind mount.
    assert "cp examples/specs/smoke.yaml" in text
    assert "/data/specs" in runtime


def test_dockerignore_keeps_the_context_small_and_the_env_out() -> None:
    entries = set(DOCKERIGNORE.read_text(encoding="utf-8").split())
    for expected in (".git", "tests/", "docs/", "wt/", ".test-work", ".env"):
        assert expected in entries


def test_env_example_holds_no_secret_default() -> None:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        assert separator == "=", f"{line!r} is not KEY=VALUE"
        values[key] = value
    assert values["EVALH_MASTER_KEY"] == ""
    assert values["EVALH_POSTGRES_PASSWORD"] == ""
    for key, value in values.items():
        if any(word in key for word in ("KEY", "PASSWORD", "SECRET", "TOKEN")):
            assert value == "", f"{key} must not ship a default secret"
