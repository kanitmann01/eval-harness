"""Live BYOK smoke test for the two first-party provider families.

Fill '.env.live' (gitignored) with at least one provider key, then run:

    python scripts/live_key_test.py

The script stores each key in the encrypted vault under '.evalh-live/',
generates the vault master key on first use, and runs a two-case spec with one
candidate per selected provider. It never prints a key, never puts one in the
spec, and never puts one in a command line. The spec contains credential names
only.

Use --check to store the keys and dry-run the spec without a provider call.
Use --strict to fail when an answer check fails, not only when the provider
call fails. Use --provider openai or --provider anthropic to run one family.
Use --spec-only to write the spec and master key without storing keys or
making a call; add the keys at /credentials, then launch from /specs.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from cryptography.fernet import Fernet
import yaml

ROOT = Path(__file__).resolve().parents[1]
"""The repository root, used for paths and the CLI working directory."""

SRC = ROOT / "src"
"""The source tree the child CLI puts on PYTHONPATH."""

DEFAULT_ENV_FILE = ROOT / ".env.live"
"""The gitignored file the operator fills with provider keys."""

DEFAULT_HOME = ROOT / ".evalh-live"
"""The disposable harness home: database, artifacts and generated specs."""

EXAMPLE_ENV_FILE = ROOT / ".env.live.example"
"""The committed template copied to .env.live when it is missing."""

SYSTEM_PROMPT = (
    "Answer with the shortest correct answer. No punctuation, no explanation."
)
"""The system prompt that keeps the two answers easy to check."""

CASES: list[dict[str, Any]] = [
    {
        "case_id": "multiply",
        "prompt": "What is 6 times 7? Reply with the number only.",
        "expected": {"answer": "42"},
        "tier": 1,
    },
    {
        "case_id": "capital",
        "prompt": "What is the capital of France? Reply with the city name only.",
        "expected": {"answer": "Paris"},
        "tier": 1,
    },
]
"""Two cheap cases: one arithmetic, one recall, both substring-checkable."""

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "credential": "openai-live",
        "provider": "openai",
        "adapter": "evalharness.adapters.candidates.openai_compat:build",
        "price_provider": "openai-compatible",
        "default_model": "gpt-4o-mini",
        "default_base_url": "https://api.openai.com/v1",
        "options": {"auth": "bearer"},
    },
    "anthropic": {
        "credential": "anthropic-live",
        "provider": "anthropic",
        "adapter": "evalharness.adapters.candidates.anthropic:build",
        "price_provider": "anthropic",
        "default_model": "claude-haiku-4-5",
        "default_base_url": "https://api.anthropic.com",
        "options": {
            "auth": "header",
            "header_name": "x-api-key",
            "max_tokens": 64,
        },
    },
}
"""The two first-party provider families and their auth shapes."""


def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines without expanding variables or printing values."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, raw = stripped.split("=", 1)
        name = name.strip()
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def setting(values: dict[str, str], name: str, default: str = "") -> str:
    """Return an environment override, then the env file, then a default."""
    return os.environ.get(name) or values.get(name) or default


def upsert_env_value(path: Path, name: str, value: str) -> None:
    """Replace one KEY= line in the env file, preserving every other line."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement = f"{name}={value}"
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith(f"{name}="):
            updated.append(replacement)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(replacement)
    path.write_text(chr(10).join(updated) + chr(10), encoding="utf-8")


def ensure_env_file(path: Path) -> bool:
    """Create the gitignored env file from the template when it is missing."""
    if path.exists():
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_ENV_FILE, path)
    return False


def ensure_master_key(path: Path, values: dict[str, str]) -> str:
    """Return the vault key, generating and persisting one when it is blank."""
    existing = os.environ.get("EVALH_MASTER_KEY") or values.get("EVALH_MASTER_KEY", "")
    if existing:
        return existing
    key = Fernet.generate_key().decode("ascii")
    upsert_env_value(path, "EVALH_MASTER_KEY", key)
    values["EVALH_MASTER_KEY"] = key
    print(
        f"generated EVALH_MASTER_KEY in {path}; back it up, losing it means "
        "re-entering every provider key"
    )
    return key


def select_providers(requested: str, values: dict[str, str]) -> list[str]:
    """Return the providers with a non-empty key; reject an explicit empty one."""
    names = ["openai", "anthropic"] if requested == "both" else [requested]
    selected: list[str] = []
    for name in names:
        key = setting(values, f"{name.upper()}_API_KEY")
        if key:
            selected.append(name)
        elif requested != "both":
            print(
                f"no {name.upper()}_API_KEY in the environment or the env file",
                file=sys.stderr,
            )
        else:
            print(f"skipping {name}: no {name.upper()}_API_KEY set", file=sys.stderr)
    return selected


def build_spec(values: dict[str, str], selected: Sequence[str]) -> dict[str, Any]:
    """Build one small spec with one candidate per selected provider."""
    credentials: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []
    provider_prices: dict[str, dict[str, Any]] = {}
    for name in selected:
        config = PROVIDERS[name]
        prefix = name.upper()
        model = setting(values, f"{prefix}_MODEL", config["default_model"])
        base_url = setting(values, f"{prefix}_BASE_URL", config["default_base_url"])
        credential = config["credential"]
        credentials.append({"name": credential, "provider": config["provider"]})
        options = {
            "base_url": base_url,
            "system": SYSTEM_PROMPT,
            "timeout_s": 60,
            **config["options"],
        }
        candidates.append(
            {
                "name": f"{name}-live",
                "adapter": config["adapter"],
                "credential": credential,
                "model": model,
                "options": options,
            }
        )
        input_price = setting(values, f"{prefix}_INPUT_PRICE")
        output_price = setting(values, f"{prefix}_OUTPUT_PRICE")
        if input_price and output_price:
            provider_prices.setdefault(config["price_provider"], {})[model] = {
                "input": float(input_price),
                "output": float(output_price),
            }
        elif input_price or output_price:
            raise ValueError(
                f"set both {prefix}_INPUT_PRICE and {prefix}_OUTPUT_PRICE, or neither"
            )
    spec: dict[str, Any] = {
        "spec_version": 1,
        "name": "live-key-test",
        "description": (
            "One cheap provider call per key; proves the vault and adapter auth path."
        ),
        "seed": 7,
        "source": {
            "adapter": "evalharness.adapters.sources.inline:build",
            "options": {"cases": CASES},
        },
        "credentials": credentials,
        "candidates": candidates,
        "replicas": 1,
        "concurrency": max(1, len(candidates)),
        "budget_usd": 1.00,
        "metrics": [
            {
                "key": "contains",
                "adapter": "evalharness.adapters.metrics.contains:build",
                "options": {},
            }
        ],
        "judges": [],
        "gates": [],
    }
    if provider_prices:
        spec["provider_prices"] = provider_prices
    return spec


def write_spec(path: Path, spec: dict[str, Any]) -> None:
    """Write the generated spec; it contains credential names, never secrets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


def run_cli(
    args: Sequence[str],
    env: dict[str, str],
    *,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one CLI command with the repository source on PYTHONPATH."""
    child_env = dict(env)
    pythonpath = str(SRC)
    if child_env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + child_env["PYTHONPATH"]
    child_env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-m", "evalharness", *args],
        cwd=ROOT,
        env=child_env,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _print_result(result: subprocess.CompletedProcess[str]) -> None:
    """Print the CLI stdout and stderr without inventing a result."""
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)


def store_credentials(
    selected: Sequence[str],
    values: dict[str, str],
    env: dict[str, str],
) -> int:
    """Replace each live credential with the key from the env file."""
    for name in selected:
        config = PROVIDERS[name]
        credential = config["credential"]
        key = setting(values, f"{name.upper()}_API_KEY")
        # A duplicate name is a spec error, so delete first. An unknown name
        # exits 2 on the first run; that is expected and stays quiet.
        run_cli(["credentials", "delete", credential, "--yes"], env)
        result = run_cli(
            [
                "credentials",
                "add",
                credential,
                "--provider",
                config["provider"],
                "--secret-stdin",
            ],
            env,
            stdin=key + chr(10),
        )
        if result.returncode != 0:
            _print_result(result)
            return result.returncode
        print(result.stdout.strip())
    return 0


def _check_counts(output: str) -> tuple[int, int] | None:
    """Read the aggregate table's per-candidate overall rows."""
    passed = 0
    total = 0
    found = False
    for line in output.splitlines():
        match = re.match(
            r"^(\S+)\s+(\S+)\s+all\s+([0-9]+)\s+([0-9.]+)%",
            line,
        )
        if match is None:
            continue
        found = True
        n = int(match.group(3))
        total += n
        passed += round(n * float(match.group(4)) / 100)
    return (passed, total) if found else None


def run_live(spec_path: Path, env: dict[str, str], *, strict: bool) -> int:
    """Run the generated spec and report whether the provider call succeeded."""
    result = run_cli(["run", str(spec_path)], env)
    _print_result(result)
    if result.returncode != 0:
        return result.returncode
    match = re.search(
        r"^run [^ ]+ status=([A-Za-z_]+) cells=([0-9]+) ok=([0-9]+)",
        result.stdout,
        re.MULTILINE,
    )
    if match is None:
        print("could not read the run status from the CLI output", file=sys.stderr)
        return 1
    status, cells, cells_ok = (
        match.group(1),
        int(match.group(2)),
        int(match.group(3)),
    )
    print(f"key test: status={status} cells={cells} cells_ok={cells_ok}")
    if status != "ok":
        print(
            "the provider call failed; the run output above has the provider's "
            "message",
            file=sys.stderr,
        )
        return 1
    counts = _check_counts(result.stdout)
    if counts is None:
        print(
            "the provider call completed, but the answer-check table could not "
            "be read",
            file=sys.stderr,
        )
        if strict:
            return 1
        return 0
    passed, total = counts
    print(f"answer checks: {passed}/{total} passed")
    if passed == total:
        print("all answer checks passed")
        return 0
    message = (
        f"the key was accepted, but {total - passed} of {total} answer checks "
        "failed. This is model quality, not authentication."
    )
    if strict:
        print(message, file=sys.stderr)
        return 1
    print(message)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, prepare the vault, and run the live smoke test."""
    parser = argparse.ArgumentParser(
        description=(
            "Store real provider keys and run a two-case live smoke test. "
            "Fill .env.live first; the script never prints a key."
        )
    )
    parser.add_argument(
        "--provider",
        choices=("openai", "anthropic", "both"),
        default="both",
        help="run one provider family or both (default: both, skipping a missing key)",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="env file to read and write (default: .env.live)",
    )
    parser.add_argument(
        "--home",
        default=str(DEFAULT_HOME),
        help="harness home for the database, artifacts and spec (default: .evalh-live)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="store the keys and dry-run the spec; make no provider call",
    )
    parser.add_argument(
        "--spec-only",
        action="store_true",
        help=(
            "write the spec and master key only; add keys at /credentials "
            "and launch from /specs"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when an answer check fails, not only when the provider call fails",
    )
    args = parser.parse_args(argv)
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    if not ensure_env_file(env_path):
        print(
            f"created {env_path}. Fill in OPENAI_API_KEY and/or "
            "ANTHROPIC_API_KEY, then rerun."
        )
        return 2
    values = load_env_file(env_path)
    if args.spec_only:
        selected = (
            ["openai", "anthropic"] if args.provider == "both" else [args.provider]
        )
        try:
            ensure_master_key(env_path, values)
            spec = build_spec(values, selected)
        except ValueError as exc:
            print(f"live key test error: {exc}", file=sys.stderr)
            return 2
        home = Path(args.home)
        if not home.is_absolute():
            home = ROOT / home
        specs = home / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        spec_path = specs / "live-key-test.yaml"
        write_spec(spec_path, spec)
        print(f"spec: {spec_path}")
        print(
            "Add the matching credentials at /credentials, then launch "
            "live-key-test from /specs."
        )
        return 0
    selected = select_providers(args.provider, values)
    if not selected:
        print(
            f"no provider keys found in {env_path} or the environment.",
            file=sys.stderr,
        )
        print(
            "fill OPENAI_API_KEY and/or ANTHROPIC_API_KEY and rerun.",
            file=sys.stderr,
        )
        return 2
    try:
        master_key = ensure_master_key(env_path, values)
        spec = build_spec(values, selected)
    except ValueError as exc:
        print(f"live key test error: {exc}", file=sys.stderr)
        return 2
    home = Path(args.home)
    if not home.is_absolute():
        home = ROOT / home
    specs = home / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    spec_path = specs / "live-key-test.yaml"
    write_spec(spec_path, spec)
    print(f"spec: {spec_path}")
    env = os.environ.copy()
    env["EVALH_HOME"] = str(home)
    env["EVALH_SPECS_DIR"] = str(specs)
    env["EVALH_MASTER_KEY"] = master_key
    code = store_credentials(selected, values, env)
    if code != 0:
        return code
    if args.check:
        result = run_cli(["run", str(spec_path), "--dry-run"], env)
        _print_result(result)
        return result.returncode
    return run_live(spec_path, env, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
