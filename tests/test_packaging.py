"""Packaging wiring: compose topology, image shape, dockerignore and env example.

The compose assertions parse the YAML and check the references between services
and named volumes rather than matching the file as text. The smoke script has
its own HTTP-seam test file because it drives a real server. The release
assertions read the package and the project metadata together so the version
and the licence cannot drift apart.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

from evalharness import __version__

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "compose.yaml"
DOCKERFILE = REPO / "Dockerfile"
ENV_EXAMPLE = REPO / ".env.example"
DOCKERIGNORE = REPO / ".dockerignore"
PYPROJECT = REPO / "pyproject.toml"
LICENSE = REPO / "LICENSE"
SPECS = REPO / "examples" / "specs"
OFFLINE_SPECS = ("smoke.yaml", "stub-pack.yaml", "gated.yaml", "gated-failing.yaml")


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
    # The image records the commit it was built from; compose forwards the
    # operator's environment and falls back to "dev" when it is unset.
    assert harness["build"]["args"]["EVALH_HARNESS_SHA"] == "${EVALH_HARNESS_SHA:-dev}"
    assert harness["ports"] == ["${EVALH_PORT:-8000}:8000"]
    # The first start without a key must exit 2 and stay exited; a restart
    # policy would print a fresh key in a loop instead of waiting for the
    # operator to put the printed key in .env.
    assert "restart" not in harness
    assert "${EVALH_POSTGRES_PASSWORD:?" in db["environment"]["POSTGRES_PASSWORD"]
    assert "${EVALH_MASTER_KEY:-}" in environment["EVALH_MASTER_KEY"]

    # Compose only reports a health state for a service that declares a probe,
    # so the harness mirrors the image's own /healthz check here as well.
    healthcheck = harness["healthcheck"]
    probe = healthcheck["test"]
    assert probe[:3] == ["CMD", "python", "-c"]
    assert "urllib.request.urlopen" in probe[3]
    assert "http://127.0.0.1:8000/healthz" in probe[3]
    assert healthcheck["interval"] == "30s"
    assert healthcheck["timeout"] == "5s"
    assert healthcheck["retries"] == 3
    assert healthcheck["start_period"] == "15s"


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
    assert "ARG EVALH_HARNESS_SHA=dev" in runtime
    assert "ENV EVALH_HARNESS_SHA=$EVALH_HARNESS_SHA" in runtime
    assert (
        'ENTRYPOINT ["evalh", "serve", "--host", "0.0.0.0", "--port", "8000"]'
        in runtime
    )
    assert 'pip install ".[postgres]"' in text
    assert "[dev]" not in text
    # The named /data volume is primed with the offline specs CI and the smoke
    # script launch, so a fresh compose project can run them without a bind
    # mount.
    assert "cp examples/specs/smoke.yaml" in text
    assert "/data/specs" in runtime
    seeding = text.split("RUN install -d", 1)[1].split("&& chown", 1)[0]
    for name in OFFLINE_SPECS:
        assert f"examples/specs/{name}" in seeding, name
        assert (SPECS / name).is_file(), name


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


def test_the_version_is_pinned_in_the_source_and_the_project_metadata() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__


def test_the_licence_is_mit_for_the_project_contributors() -> None:
    text = LICENSE.read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 evalharness contributors" in text
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["license"] == {"text": "MIT"}
