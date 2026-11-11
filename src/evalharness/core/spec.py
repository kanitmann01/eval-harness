"""YAML loading, schema versioning, semantic validation and price resolution."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, cast
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from evalharness.core.credential_names import unknown_credential
from evalharness.core.models import (
    CandidateSpec,
    ContextChunk,
    ContextSpec,
    CredentialState,
    Price,
    Spec,
)
from evalharness.core.prices import (
    PriceTable,
    load_default_prices,
    resolve_price,
    resolved_price_table,
)
from evalharness.core.protocols import (
    Capabilities,
    ContextSelector,
    Judge,
    Metric,
    Source,
)
from evalharness.core.registry import AdapterFactory, resolve
from evalharness.defaults import DEFAULT_SINK_ADAPTER
from evalharness.errors import RegistryError, SpecError

SPEC_VERSION = 1
"""The only spec version this harness understands."""


@dataclass(frozen=True, slots=True)
class ResolvedSpec:
    """A structurally and semantically valid spec plus its resolved adapters."""

    spec: Spec
    raw_text: str
    sha: str
    prices: dict[str, Price]
    price_table: PriceTable
    candidate_prices: dict[str, Price]
    candidate_providers: dict[str, str]
    judge_prices: dict[str, Price]
    judge_providers: dict[str, str]
    candidates: dict[str, CandidateSpec]
    candidate_factories: dict[str, AdapterFactory]
    source: Source
    metrics: dict[str, Metric]
    judges: dict[str, Judge]
    sink_factory: AdapterFactory
    sink_options: dict[str, Any]
    prompt_overrides: dict[str, str | None]
    chunks: dict[str, list[ContextChunk]]
    candidate_contexts: dict[str, str | None]
    context_selectors: dict[str, ContextSelector]
    warnings: list[str] = field(default_factory=list)


def parse_spec(text: str) -> Spec:
    """Parse YAML text into the structural spec model."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(
            "spec",
            "<yaml>",
            f"the YAML did not parse: {exc}",
            "fix the YAML syntax",
        ) from exc
    if not isinstance(data, dict):
        raise SpecError(
            "spec",
            data,
            "the spec must be a YAML mapping",
            "start the file with spec_version: 1",
        )
    try:
        spec = Spec.model_validate(data)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error.get("loc", ())) or "spec"
        raise SpecError(
            field,
            error.get("input"),
            str(error.get("msg", "invalid value")),
            "see docs/spec-v1.md",
        ) from exc
    if spec.spec_version != SPEC_VERSION:
        raise SpecError(
            "spec_version",
            spec.spec_version,
            f"unsupported spec version; this harness supports {SPEC_VERSION}",
            "set spec_version: 1",
        )
    return spec


def read_spec(path: str | Path) -> str:
    """Read a spec file as UTF-8 text, raising an actionable spec error."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(
            "spec",
            str(path),
            f"the spec file could not be read: {exc}",
            "check the path and permissions",
        ) from exc


def load_spec(path: str | Path) -> Spec:
    """Read and structurally parse a spec file."""
    return parse_spec(read_spec(path))


def spec_sha(text: str) -> str:
    """Return the sha256 of the spec text, used to identify a snapshot."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_and_validate(
    path: str | Path,
    *,
    stored_credentials: Iterable[str] = (),
    credential_states: Iterable[CredentialState] = (),
    require_verified_credentials: bool = False,
) -> ResolvedSpec:
    """Load a spec file and validate it in one step.

    `stored_credentials` names credentials present in the vault; a candidate or
    judge may use one without declaring it again in the spec. `credential_states`
    adds the vault's usability state, and `require_verified_credentials` makes a
    never-tested credential a validation error (the console's spec editor).
    """
    raw_text = read_spec(path)
    return validate_spec(
        parse_spec(raw_text),
        raw_text=raw_text,
        stored_credentials=stored_credentials,
        credential_states=credential_states,
        require_verified_credentials=require_verified_credentials,
        spec_dir=Path(path).parent,
    )


def _resolve_factory(path: str, field: str) -> AdapterFactory:
    try:
        return resolve(path)
    except RegistryError as exc:
        raise SpecError(field, path, exc.reason, exc.fix) from exc


def _build(path: str, identity: str, options: dict[str, Any], field: str) -> Any:
    factory = _resolve_factory(path, field)
    try:
        return factory(identity, dict(options))
    except Exception as exc:  # adapter factories are external code
        raise SpecError(
            field,
            path,
            f"the adapter factory raised: {exc}",
            "fix the adapter or its options",
        ) from exc


def _unique(values: Iterable[str], field: str, noun: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise SpecError(
                field,
                value,
                f"duplicate {noun} name",
                f"give every {noun} a unique name",
            )
        seen.add(value)


def _require_price(
    spec: Spec,
    provider: str,
    model: str,
    defaults: PriceTable,
) -> Price:
    """Return the resolved price, or fail naming the model and where to add one.

    The shipped table and the spec's provider_prices both count as a price; a
    model absent from all three sources is a validation error before any spend.
    """
    price = resolve_price(spec, provider, model, defaults)
    if price is not None:
        return price
    where = f" at provider {provider!r}" if provider else ""
    if provider:
        fix = (
            f"add provider_prices.{provider}.{model}: "
            "{input: <usd_per_mtok>, output: <usd_per_mtok>}, or an entry "
            f"for {model!r} in the shipped config/prices.yaml"
        )
    else:
        fix = (
            f"add prices.{model}: "
            "{input: <usd_per_mtok>, output: <usd_per_mtok>}"
        )
    raise SpecError(
        f"prices.{model}",
        model,
        f"no price is declared for this model{where}",
        fix,
    )


def _declared_credential(
    known: set[str],
    name: str | None,
    field: str,
) -> None:
    """Raise the shared unknown-credential error for an undeclared name."""
    if name is not None and name not in known:
        raise unknown_credential(name, field=field, available=known)


def _usable_credential(
    states: dict[str, CredentialState],
    name: str | None,
    field: str,
    *,
    require_verified: bool,
) -> None:
    """Refuse a stored credential the vault marks disabled or never verified.

    A name the vault does not store is left to _declared_credential: a spec
    declaration alone keeps working for an adapter that needs no secret.
    """
    if name is None:
        return
    state = states.get(name)
    if state is None:
        return
    if state.disabled:
        raise SpecError(
            field,
            name,
            "the credential is disabled, so the harness must not use its secret",
            "enable it at /credentials, then save the spec again",
        )
    if require_verified and not state.verified:
        raise SpecError(
            field,
            name,
            "the credential has never been verified, so its secret is unproven",
            "test it at /credentials, then save the spec again",
        )


def _validate_repos(
    spec: Spec,
    known_credentials: set[str],
    states: dict[str, CredentialState],
    *,
    require_verified: bool,
    spec_dir: Path | None,
) -> None:
    """Validate every repository declaration before a case is loaded.

    The checks are deliberately split: shape is always checked, while the
    existence of a local root is checked only when the spec file has a
    directory. That lets the console validate a pasted spec without inventing
    a working directory, and lets the snapshot step be the final authority.
    """
    for name, repo in spec.repos.items():
        field = f"repos.{name}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise SpecError(
                field,
                name,
                "a repository name must start with a letter or digit and "
                "contain only letters, digits, dots, underscores and hyphens",
                "rename the repository under repos:",
            )
        if repo.url is not None:
            _validate_repo_url(repo.url, field, spec_dir)
        else:
            _validate_repo_root(repo.root, field, spec_dir)
        if repo.credential is not None and repo.url is None:
            raise SpecError(
                f"{field}.credential",
                repo.credential,
                "a credential can only authenticate a remote repository",
                "remove credential or set url to an https repository URL",
            )
        if repo.ref is not None and repo.url is None:
            raise SpecError(
                f"{field}.ref",
                repo.ref,
                "a git ref only means something for a remote repository",
                "remove ref or set url to an https repository URL",
            )
        if repo.ref is not None:
            _validate_repo_ref(repo.ref, f"{field}.ref")
        if not repo.include:
            raise SpecError(
                f"{field}.include",
                repo.include,
                "include must name at least one glob",
                'set include to ["**/*"] to snapshot every file',
            )
        for pattern in repo.include:
            _validate_repo_glob(pattern, f"{field}.include", spec_dir)
        for pattern in repo.exclude:
            _validate_repo_glob(pattern, f"{field}.exclude", spec_dir)
        _declared_credential(known_credentials, repo.credential, f"{field}.credential")
        _usable_credential(
            states,
            repo.credential,
            f"{field}.credential",
            require_verified=require_verified,
        )


def _validate_repo_url(url: str, field: str, spec_dir: Path | None) -> None:
    """Require an https URL with no embedded identity, query or fragment."""
    del spec_dir
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SpecError(
            f"{field}.url",
            url,
            "a repository URL must be https with a host",
            "use https://host/owner/repo.git",
        )
    if parts.username is not None or parts.password is not None:
        raise SpecError(
            f"{field}.url",
            url,
            "a repository URL must not embed a username or password",
            "remove the userinfo and declare credential: instead",
        )
    if parts.query or parts.fragment:
        raise SpecError(
            f"{field}.url",
            url,
            "a repository URL must not carry a query or fragment",
            "remove the query and fragment",
        )


def _validate_repo_ref(ref: str, field: str) -> None:
    """Require a ref git accepts as a name, never as an option or a range."""
    forbidden = set(" \t\r\n\x00~^:?*[\\")
    reason = ""
    if not ref:
        reason = "a git ref must not be empty"
    elif ref.startswith("-"):
        reason = "a git ref must not start with a hyphen"
    elif any(character in forbidden for character in ref):
        reason = "a git ref must not contain whitespace, NUL or ~^:?*[\\"
    elif ".." in ref or "@{" in ref:
        reason = "a git ref must not contain '..' or '@{'"
    elif ref.endswith(".") or ref.endswith("/") or ref.startswith("/"):
        reason = "a git ref must not start or end with a slash or dot"
    elif "//" in ref:
        reason = "a git ref must not contain an empty path segment"
    if reason:
        raise SpecError(
            field,
            ref,
            reason,
            "use a branch or tag name such as 'main' or 'v1.2.3'",
        )


def _validate_repo_root(
    root: str, field: str, spec_dir: Path | None
) -> None:
    """Require a relative, contained local root that exists when it can."""
    if "\x00" in root:
        raise SpecError(
            f"{field}.root",
            root,
            "a repository root must not contain a NUL byte",
            "use a normal relative path such as '.'",
        )
    if root.startswith("~"):
        raise SpecError(
            f"{field}.root",
            root,
            "a repository root must not use a home-directory shortcut",
            "use a path relative to the spec file",
        )
    if PurePosixPath(root).is_absolute() or PureWindowsPath(root).is_absolute():
        raise SpecError(
            f"{field}.root",
            root,
            "a repository root must be relative to the spec file",
            "use a path such as '.' or 'fixtures/repo'",
        )
    if PureWindowsPath(root).drive:
        raise SpecError(
            f"{field}.root",
            root,
            "a repository root must not carry a drive letter",
            "use a path relative to the spec file",
        )
    if spec_dir is not None and not (spec_dir / root).is_dir():
        raise SpecError(
            f"{field}.root",
            root,
            "the repository root does not exist or is not a directory",
            "fix the path or create the directory before loading the spec",
        )


def _validate_repo_glob(
    pattern: str, field: str, spec_dir: Path | None
) -> None:
    """Reject a glob that could escape the repository tree."""
    del spec_dir
    normalized = pattern.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not pattern:
        raise SpecError(
            field,
            pattern,
            "a repository glob must not be empty",
            'use a pattern such as "**/*.py"',
        )
    if (
        normalized.startswith("/")
        or normalized.startswith("~")
        or ".." in parts
        or PureWindowsPath(pattern).is_absolute()
        or PureWindowsPath(pattern).drive
    ):
        raise SpecError(
            field,
            pattern,
            "a repository glob must stay inside the repository",
            'use a relative pattern such as "**/*.py"',
        )


def _validate_source_credential(
    spec: Spec,
    known_credentials: set[str],
    states: dict[str, CredentialState],
    *,
    require_verified: bool,
) -> None:
    """Allow source.credential only when a remote repository needs it."""
    name = spec.source.credential
    if name is None:
        return
    if not any(repo.url is not None for repo in spec.repos.values()):
        raise SpecError(
            "source.credential",
            name,
            "a source credential only authenticates a remote repository",
            "remove source.credential or declare a repository with an https url",
        )
    if name not in known_credentials:
        raise unknown_credential(
            name, field="source.credential", available=known_credentials
        )
    _usable_credential(
        states,
        name,
        "source.credential",
        require_verified=require_verified,
    )


def _candidate_axes(
    spec: Spec,
    candidate: CandidateSpec,
    capabilities: Capabilities,
    index: int,
) -> tuple[str | None, list[ContextChunk], str | None]:
    options = candidate.options
    prompt_name = options.get("prompt")
    override: str | None = None
    if prompt_name is not None:
        field = f"candidates[{index}].options.prompt"
        if not isinstance(prompt_name, str):
            raise SpecError(
                field,
                prompt_name,
                "a prompt variant must be a string name",
                "name a variant declared under prompts:",
            )
        if prompt_name not in spec.prompts:
            choices = ", ".join(sorted(spec.prompts)) or "none declared"
            raise SpecError(
                field,
                prompt_name,
                "unknown prompt variant",
                f"declare prompts.{prompt_name} or use one of: {choices}",
            )
        if not capabilities.supports_prompt_override:
            raise SpecError(
                field,
                candidate.adapter,
                "the candidate cannot accept a prompt override",
                "remove options.prompt or use a candidate that supports overrides",
            )
        override = spec.prompts[prompt_name]

    context_name = options.get("context")
    chunks: list[ContextChunk] = []
    selected: str | None = None
    if context_name is not None:
        field = f"candidates[{index}].options.context"
        if isinstance(context_name, str):
            if context_name not in spec.contexts:
                choices = ", ".join(sorted(spec.contexts)) or "none declared"
                raise SpecError(
                    field,
                    context_name,
                    "unknown context",
                    f"declare contexts.{context_name} or use one of: {choices}",
                )
            declared = spec.contexts[context_name]
            if isinstance(declared, ContextSpec):
                selected = context_name
            else:
                chunks = list(declared)
        elif isinstance(context_name, dict) and isinstance(
            context_name.get("chunks"), list
        ):
            try:
                chunks = [
                    ContextChunk.model_validate(chunk)
                    for chunk in context_name["chunks"]
                ]
            except ValidationError as exc:
                raise SpecError(
                    field,
                    context_name,
                    f"inline context is malformed: {exc.errors()[0].get('msg')}",
                    "give every chunk a chunk_id and a text",
                ) from exc
        else:
            raise SpecError(
                field,
                context_name,
                "context must be a declared context name or {chunks: [...]}",
                "set context to a name under contexts:",
            )
        if not capabilities.supports_context_injection:
            raise SpecError(
                field,
                candidate.adapter,
                "the candidate cannot accept context injection",
                "remove options.context or use a candidate that supports injection",
            )
    return override, chunks, selected


def validate_spec(
    spec: Spec,
    raw_text: str = "",
    *,
    stored_credentials: Iterable[str] = (),
    credential_states: Iterable[CredentialState] = (),
    require_verified_credentials: bool = False,
    default_prices: PriceTable | None = None,
    spec_dir: Path | None = None,
) -> ResolvedSpec:
    """Validate every declarative reference before anything executes.

    A credential is known when the spec declares it or the vault stores it.
    `stored_credentials` is the vault's name list, never its secrets;
    `credential_states` is its usability state. A candidate or judge that
    declares `requires_credential` needs its name in the vault, because a spec
    declaration alone cannot supply the secret; a judge that declares
    `requires_model` needs a declared model. A stored credential that is
    disabled is always refused; one that has never passed a test is refused
    when `require_verified_credentials` is set, which is what the console does.

    `default_prices` is the shipped provider table. Tests pass one explicitly;
    production loads `config/prices.yaml` from the installed package.

    `spec_dir` is the directory holding the spec file. A local repository root
    is resolved against it and must exist; validation without a path (a pasted
    spec in the console) still checks the root's shape, and the snapshot step
    checks that it exists.
    """
    defaults = default_prices if default_prices is not None else load_default_prices()
    _unique(
        (candidate.name for candidate in spec.candidates), "candidates", "candidate"
    )
    _unique((metric.key for metric in spec.metrics), "metrics", "metric")
    _unique((judge.name for judge in spec.judges), "judges", "judge")
    _unique(
        (credential.name for credential in spec.credentials),
        "credentials",
        "credential",
    )

    states = {state.name: state for state in credential_states}
    stored_names = set(stored_credentials)
    stored_names.update(states)
    known_credentials = {credential.name for credential in spec.credentials}
    known_credentials.update(stored_names)

    for index, candidate in enumerate(spec.candidates):
        field = f"candidates[{index}].credential"
        _declared_credential(known_credentials, candidate.credential, field)
        _usable_credential(
            states,
            candidate.credential,
            field,
            require_verified=require_verified_credentials,
        )

    for index, judge in enumerate(spec.judges):
        field = f"judges[{index}].credential"
        _declared_credential(known_credentials, judge.credential, field)
        _usable_credential(
            states,
            judge.credential,
            field,
            require_verified=require_verified_credentials,
        )

    _validate_repos(
        spec,
        known_credentials,
        states,
        require_verified=require_verified_credentials,
        spec_dir=spec_dir,
    )
    _validate_source_credential(
        spec,
        known_credentials,
        states,
        require_verified=require_verified_credentials,
    )

    source = _build(
        spec.source.adapter,
        "source",
        spec.source.options,
        "source.adapter",
    )
    context_selectors: dict[str, ContextSelector] = {}
    for name, entry in spec.contexts.items():
        if not isinstance(entry, ContextSpec):
            continue
        instance = _build(
            entry.adapter,
            name,
            entry.options,
            f"contexts.{name}.adapter",
        )
        if not callable(getattr(instance, "select", None)):
            raise SpecError(
                f"contexts.{name}.adapter",
                entry.adapter,
                "the context adapter did not expose select(case, ctx)",
                "return an object with a select method that yields ContextChunk values",
            )
        context_selectors[name] = cast(ContextSelector, instance)

    candidate_factories: dict[str, AdapterFactory] = {}
    prompt_overrides: dict[str, str | None] = {}
    chunks_by_candidate: dict[str, list[ContextChunk]] = {}
    candidate_contexts: dict[str, str | None] = {}
    candidate_prices: dict[str, Price] = {}
    candidate_providers: dict[str, str] = {}
    for index, candidate in enumerate(spec.candidates):
        factory = _resolve_factory(candidate.adapter, f"candidates[{index}].adapter")
        instance = _build(
            candidate.adapter,
            candidate.name,
            candidate.options,
            f"candidates[{index}].adapter",
        )
        capabilities = getattr(instance, "capabilities", None)
        if not isinstance(capabilities, Capabilities):
            raise SpecError(
                f"candidates[{index}].adapter",
                candidate.adapter,
                "the candidate adapter did not expose Capabilities",
                "return Candidate.capabilities as a Capabilities instance",
            )
        if (
            capabilities.requires_credential
            and candidate.credential not in stored_names
        ):
            raise SpecError(
                f"candidates[{index}].credential",
                candidate.credential,
                f"candidate {candidate.name!r} needs a credential resolved "
                "from the vault, but none is stored under that name",
                "add it on the console Credentials page (/credentials) or with "
                "'evalh credentials add', or set options.auth: none when the "
                "endpoint holds its own key",
            )
        override, chunks, context = _candidate_axes(
            spec, candidate, capabilities, index
        )
        candidate_factories[candidate.name] = factory
        prompt_overrides[candidate.name] = override
        chunks_by_candidate[candidate.name] = chunks
        candidate_contexts[candidate.name] = context
        candidate_providers[candidate.name] = capabilities.provider
        candidate_prices[candidate.name] = _require_price(
            spec, capabilities.provider, candidate.model, defaults
        )

    metrics: dict[str, Metric] = {}
    for index, metric_spec in enumerate(spec.metrics):
        instance = _build(
            metric_spec.adapter,
            metric_spec.key,
            metric_spec.options,
            f"metrics[{index}].adapter",
        )
        if getattr(instance, "key", None) != metric_spec.key:
            raise SpecError(
                f"metrics[{index}].key",
                metric_spec.key,
                "the metric adapter reports a different key",
                f"return key={metric_spec.key!r} from the adapter or align the spec",
            )
        judge_name = metric_spec.options.get("judge")
        if judge_name is not None:
            declared = {judge.name for judge in spec.judges}
            if judge_name not in declared:
                choices = ", ".join(sorted(declared)) or "none declared"
                raise SpecError(
                    f"metrics[{index}].options.judge",
                    judge_name,
                    "the metric names an undeclared judge",
                    f"declare judges: {judge_name} or use one of: {choices}",
                )
        metrics[metric_spec.key] = instance

    judges: dict[str, Judge] = {}
    judge_prices: dict[str, Price] = {}
    judge_providers: dict[str, str] = {}
    for index, judge_spec in enumerate(spec.judges):
        instance = _build(
            judge_spec.adapter,
            judge_spec.name,
            judge_spec.options,
            f"judges[{index}].adapter",
        )
        capabilities = getattr(instance, "capabilities", None)
        if not isinstance(capabilities, Capabilities):
            capabilities = Capabilities()
        if capabilities.requires_model and judge_spec.model is None:
            raise SpecError(
                f"judges[{index}].model",
                None,
                f"judge {judge_spec.name!r} calls a provider but declares no model",
                "set model: <the provider model id> and give it a price",
            )
        if (
            capabilities.requires_credential
            and judge_spec.credential not in stored_names
        ):
            raise SpecError(
                f"judges[{index}].credential",
                judge_spec.credential,
                f"judge {judge_spec.name!r} needs a credential resolved from "
                "the vault, but none is stored under that name",
                "add it on the console Credentials page (/credentials) or with "
                "'evalh credentials add', or use a judge that needs no "
                "credential",
            )
        judge_providers[judge_spec.name] = capabilities.provider
        if judge_spec.model is not None:
            judge_prices[judge_spec.name] = _require_price(
                spec, capabilities.provider, judge_spec.model, defaults
            )
        judges[judge_spec.name] = instance

    metric_keys = set(metrics)
    candidate_names = {candidate.name for candidate in spec.candidates}
    gate_keys: set[tuple[str | None, str]] = set()
    warnings: list[str] = []
    for index, gate in enumerate(spec.gates):
        if gate.metric not in metric_keys:
            choices = ", ".join(sorted(metric_keys)) or "none declared"
            raise SpecError(
                f"gates[{index}].metric",
                gate.metric,
                "the gate names an undeclared metric",
                f"use one of: {choices}",
            )
        if gate.candidate is not None and gate.candidate not in candidate_names:
            choices = ", ".join(sorted(candidate_names)) or "none declared"
            raise SpecError(
                f"gates[{index}].candidate",
                gate.candidate,
                "the gate names an undeclared candidate",
                f"use one of: {choices}, or omit candidate for the run-wide gate",
            )
        if gate.min is None and gate.max is None:
            raise SpecError(
                f"gates[{index}]",
                gate.metric,
                "the gate has neither min nor max",
                "set min, max, or both",
            )
        key = (gate.candidate, gate.metric)
        if key in gate_keys:
            scope = f"candidate {gate.candidate!r}" if gate.candidate else "the run"
            raise SpecError(
                f"gates[{index}].metric",
                gate.metric,
                f"duplicate gate for this metric on {scope}",
                "combine the bounds into one gate",
            )
        gate_keys.add(key)
        if "latency" in gate.metric and spec.concurrency > 1:
            warnings.append(
                "latency gate with concurrency > 1: latency measures queueing, "
                "not the model; use concurrency 1 for latency comparisons"
            )

    sink_path = spec.sink.adapter if spec.sink is not None else DEFAULT_SINK_ADAPTER
    sink_options = dict(spec.sink.options) if spec.sink is not None else {}
    sink_factory = _resolve_factory(
        sink_path,
        "sink.adapter" if spec.sink is not None else "sink",
    )

    price_table = resolved_price_table(spec, defaults)
    flat_prices: dict[str, Price] = {}
    for models in price_table.providers.values():
        flat_prices.update(models)
    return ResolvedSpec(
        spec=spec,
        raw_text=raw_text,
        sha=spec_sha(raw_text),
        prices=flat_prices,
        price_table=price_table,
        candidate_prices=candidate_prices,
        candidate_providers=candidate_providers,
        judge_prices=judge_prices,
        judge_providers=judge_providers,
        candidates={candidate.name: candidate for candidate in spec.candidates},
        candidate_factories=candidate_factories,
        source=source,
        metrics=metrics,
        judges=judges,
        sink_factory=sink_factory,
        sink_options=sink_options,
        prompt_overrides=prompt_overrides,
        chunks=chunks_by_candidate,
        candidate_contexts=candidate_contexts,
        context_selectors=context_selectors,
        warnings=warnings,
    )


def validate_yaml_text(
    text: str,
    *,
    stored_credentials: Iterable[str] = (),
    credential_states: Iterable[CredentialState] = (),
    require_verified_credentials: bool = False,
) -> ResolvedSpec:
    """Validate YAML text in memory exactly as `evalh validate` validates a file.

    The console's spec editor calls this function with the vault's credential
    states and `require_verified_credentials`, so a field error reads the same
    in the browser as it does on the command line.
    """
    return validate_spec(
        parse_spec(text),
        raw_text=text,
        stored_credentials=stored_credentials,
        credential_states=credential_states,
        require_verified_credentials=require_verified_credentials,
    )
