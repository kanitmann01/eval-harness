# Repository rules for agents

This repository is a product, not a scratch pad. Every change is reviewable, tested and
documented. Read [docs/architecture.md](docs/architecture.md) before writing code.

## Non-negotiables

1. **Tests at 100% line coverage of `src/evalharness`.** `python -m pytest` is the gate.
   `# pragma: no cover` is allowed only for `if __name__ == "__main__":` guards and
   genuinely unreachable defensive branches, each with a one-line comment saying why.
2. **Only external behaviour is tested.** Drive the CLI, the HTTP API, or a stub server.
   Never assert on a private helper, never monkeypatch a candidate, never reach into
   executor internals.
3. **Docstrings on every public module, class and function.** One line of summary, then
   parameters only where the type does not explain itself. Comments explain *why*.
4. **Adapters behind protocols.** Core imports the registry and the protocols only. A
   concrete adapter is never imported by core. New capability is a new adapter, not a
   new branch in the executor.
5. **No secrets in logs, events, artifacts, cell records or HTML.** Everything that can
   surface a string goes through the redactor.
6. **Errors are actionable.** A validation error names the field, the value and the fix.
   Exit codes are the contract for automation: 0 success, 1 gate failure, 2 spec
   error, 3 budget abort (see `evalharness.exit_codes`).
7. **The spec is the source of truth.** Interfaces hold no state the spec file does not.
   A run snapshots the spec and the price table.
8. **Type hints everywhere**, `from __future__ import annotations`, dataclasses or
   pydantic models at the boundaries, SQLAlchemy for storage.
9. **No new runtime dependency** without a reason recorded in `pyproject.toml` and a
   line in the pull request. Console pages ship server-rendered: no Node, no SPA build.
10. **Commit in small logical steps** on the branch you were given, message in the
    imperative mood, and never push or merge: the orchestrator does that.

## Local commands

```bash
python -m pytest                      # full suite with coverage gate
python -m pytest tests/test_x.py -x   # one file
python -m evalharness --help          # CLI
python -m evalharness serve           # console on http://127.0.0.1:8000
```

## Style

- Python 3.11+, `ruff`-clean shape (88 columns, double quotes, sorted imports) even if
  ruff is not installed locally.
- Prefer small modules with one responsibility over large ones.
- Public API of a module is what its docstring promises; keep the rest private.
