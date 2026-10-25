"""Retry semantics, driven through the CLI and registered test adapters."""

from __future__ import annotations

import tests.adapters  # noqa: F401 - importing registers the test adapters
from evalharness.cli import main
from tests.helpers import base_spec, open_repository, run_id_from, write_spec


def _single_candidate_spec(adapter: str, options: dict | None = None) -> dict:
    spec = base_spec()
    spec["candidates"] = [spec["candidates"][0]]
    spec["candidates"][0]["adapter"] = adapter
    spec["candidates"][0]["options"] = options or {}
    return spec


def test_transport_error_is_retried(evalh_home, workdir, capsys):
    spec = write_spec(
        workdir / "flaky.yaml",
        _single_candidate_spec("test:flaky"),
    )

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.status == "ok"
        assert cell.answer == "42"
        assert cell.output["artifacts"]["attempts"] == 2
        assert repository.list_checks(run_id, cell.cell_id)[0].passed is True
    finally:
        database.dispose()


def test_wrong_answer_is_not_retried(evalh_home, workdir, capsys):
    spec = write_spec(
        workdir / "wrong.yaml",
        _single_candidate_spec("test:wrong"),
    )

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.status == "ok"
        assert cell.answer == "wrong"
        assert cell.output["artifacts"]["attempts"] == 1
        assert repository.list_checks(run_id, cell.cell_id)[0].passed is False
    finally:
        database.dispose()


def test_non_transport_exception_is_recorded(evalh_home, workdir, capsys):
    spec = write_spec(
        workdir / "boom.yaml",
        _single_candidate_spec("test:exploding"),
    )

    code = main(["run", str(spec)])

    captured = capsys.readouterr()
    assert code == 0
    run_id = run_id_from(captured.out)
    database, repository = open_repository()
    try:
        cell = repository.list_cells(run_id)[0]
        assert cell.status == "error"
        assert cell.output["error_type"] == "ValueError"
    finally:
        database.dispose()