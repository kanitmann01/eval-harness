"""The manifest is the CI gate: every first-party adapter runs the suite."""

from __future__ import annotations

from evalharness.adapters.manifest import MANIFEST, first_party_factories
from evalharness.conformance import run_all


def test_manifest_lists_every_first_party_factory():
    assert first_party_factories() == {
        (entry.adapter, entry.kind) for entry in MANIFEST
    }


def test_every_manifest_adapter_passes_the_public_suite(evalh_home):
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
