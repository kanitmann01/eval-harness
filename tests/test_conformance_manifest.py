"""The manifest is the CI gate: every first-party adapter runs the suite."""

from __future__ import annotations

from evalharness.adapters.manifest import (
    DEFAULT_HTTP_CONFORMANCE_BASE_URL,
    DEFAULT_PROVIDER_CONFORMANCE_BASE_URL,
    HTTP_CANDIDATE,
    HTTP_CONFORMANCE_ENV,
    MANIFEST,
    PROVIDER_CONFORMANCE_ENV,
    PROVIDER_OPENAI_CANDIDATE,
    ManifestEntry,
    conformance_options,
    first_party_factories,
)
from evalharness.conformance import run_all


def test_manifest_lists_every_first_party_factory():
    assert first_party_factories() == {
        (entry.adapter, entry.kind) for entry in MANIFEST
    }


def _http_entry(base_url: str | None = None) -> ManifestEntry:
    options: dict[str, object] = {"auth": "none"}
    if base_url is not None:
        options["base_url"] = base_url
    return ManifestEntry(HTTP_CANDIDATE, "candidate", options)


def test_http_conformance_endpoint_comes_from_the_environment():
    overridden = conformance_options(
        _http_entry(None), {HTTP_CONFORMANCE_ENV: "http://127.0.0.1:9911"}
    )
    default = conformance_options(
        _http_entry(DEFAULT_HTTP_CONFORMANCE_BASE_URL), {}
    )
    explicit = conformance_options(
        _http_entry("http://stub:9"),
        {HTTP_CONFORMANCE_ENV: "http://127.0.0.1:9911"},
    )

    assert overridden["base_url"] == "http://127.0.0.1:9911"
    assert default["base_url"] == DEFAULT_HTTP_CONFORMANCE_BASE_URL
    assert default["auth"] == "none"
    assert explicit["base_url"] == "http://stub:9"


def _provider_entry(base_url: str | None = None) -> ManifestEntry:
    options: dict[str, object] = {"auth": "none"}
    if base_url is not None:
        options["base_url"] = base_url
    return ManifestEntry(PROVIDER_OPENAI_CANDIDATE, "candidate", options)


def test_provider_conformance_endpoint_comes_from_the_environment():
    overridden = conformance_options(
        _provider_entry(None),
        {PROVIDER_CONFORMANCE_ENV: "http://127.0.0.1:9912"},
    )
    default = conformance_options(
        _provider_entry(DEFAULT_PROVIDER_CONFORMANCE_BASE_URL), {}
    )
    explicit = conformance_options(
        _provider_entry("http://stub:9"),
        {PROVIDER_CONFORMANCE_ENV: "http://127.0.0.1:9912"},
    )

    assert overridden["base_url"] == "http://127.0.0.1:9912"
    assert default["base_url"] == DEFAULT_PROVIDER_CONFORMANCE_BASE_URL
    assert default["auth"] == "none"
    assert explicit["base_url"] == "http://stub:9"


def test_every_manifest_adapter_passes_the_public_suite(
    evalh_home, http_conformance_stub, provider_conformance_stub
):
    reports = run_all()

    assert len(reports) == len(MANIFEST)
    failures = {
        report.adapter: [
            result.clause for result in report.results if not result.ok
        ]
        for report in reports
        if not report.passed
    }
    assert failures == {}
