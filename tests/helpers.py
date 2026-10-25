"""Spec factories and CLI read-back helpers shared by tests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from evalharness.core.config import Settings
from evalharness.storage.db import Database
from evalharness.storage.repository import Repository

TEST_MASTER_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
"""A fixed, valid 32-byte Fernet key used by tests; never a real secret."""

INLINE_SOURCE = "evalharness.adapters.sources.inline:build"
STUB = "evalharness.adapters.candidates.stub:build"
STUB_SLOW = "evalharness.adapters.candidates.stub:build_slow"
STUB_FAILING = "evalharness.adapters.candidates.stub:build_failing"
STUB_FIXED_PROMPT = "evalharness.adapters.candidates.stub:build_fixed_prompt"
EXACT_MATCH = "evalharness.adapters.metrics.exact_match:build"
CONTAINS = "evalharness.adapters.metrics.contains:build"
CITATION_SUPPORT = "evalharness.adapters.metrics.citation_support:build"
SCRIPTED_JUDGE = "evalharness.adapters.judges.scripted:build"
JSON_SINK = "evalharness.adapters.sinks.json_sink:build"


def base_spec() -> dict[str, Any]:
    """Return a valid two-candidate, one-case smoke spec as a dict."""
    return {
        "spec_version": 1,
        "name": "test-spec",
        "description": "fixture",
        "seed": 7,
        "source": {
            "adapter": INLINE_SOURCE,
            "options": {
                "cases": [
                    {
                        "case_id": "case-1",
                        "prompt": "What is 6 times 7?",
                        "expected": {"answer": "42"},
                        "tier": 1,
                    }
                ]
            },
        },
        "prompts": {
            "concise": "Answer with digits only.",
            "explained": "Answer with a short clause.",
        },
        "contexts": {
            "none": [],
            "policy": [{"chunk_id": "policy-1", "text": "Answer as a bare number."}],
        },
        "credentials": [{"name": "stub", "provider": "none"}],
        "candidates": [
            {
                "name": "stub-concise",
                "adapter": STUB,
                "credential": "stub",
                "model": "stub-1",
                "options": {"prompt": "concise", "context": "none"},
            },
            {
                "name": "stub-explained",
                "adapter": STUB,
                "credential": "stub",
                "model": "stub-1",
                "options": {"prompt": "explained", "context": "policy"},
            },
        ],
        "replicas": 1,
        "concurrency": 1,
        "budget_usd": 1.0,
        "metrics": [{"key": "exact_match", "adapter": EXACT_MATCH, "options": {}}],
        "judges": [],
        "gates": [],
        "prices": {"stub-1": {"input": 0.15, "output": 0.60}},
    }


def write_spec(path: Path, spec: dict[str, Any]) -> Path:
    """Write a spec dict to path as YAML and return the path."""
    path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return path


def run_id_from(stdout: str) -> str:
    """Extract the run id from the CLI run table."""
    match = re.search(r"^run ([0-9a-f]+) ", stdout, re.MULTILINE)
    assert match is not None, stdout
    return match.group(1)


def open_repository() -> tuple[Database, Repository]:
    """Open the repository the CLI just wrote, using the same env settings."""
    settings = Settings.from_env()
    database = Database(settings.database_url)
    database.create_all()
    return database, Repository(database)
