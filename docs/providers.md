# Providers

Two provider families ship as first-party adapters, for candidates and for
judges: an **OpenAI-compatible** chat-completions endpoint and an
**Anthropic-style** messages endpoint. One run can compare a candidate at each
provider and judge it with a model at each provider, under two credentials,
priced with each provider's rates and attributed per provider.

The transport policy is one module, [adapters/transport.py](../src/evalharness/adapters/transport.py);
the wire dialects are one module, [adapters/provider_wire.py](../src/evalharness/adapters/provider_wire.py).
A new provider is a dialect plus four thin factories, not a new branch in the
executor.

## The provider matrix

| Kind | Adapter path | Provider | Path | Auth shape |
| --- | --- | --- | --- | --- |
| candidate | `evalharness.adapters.candidates.openai_compat:build` | `openai-compatible` | `/chat/completions` | bearer |
| candidate | `evalharness.adapters.candidates.anthropic:build` | `anthropic` | `/v1/messages` | header key |
| judge | `evalharness.adapters.judges.openai_compat:build` | `openai-compatible` | `/chat/completions` | bearer |
| judge | `evalharness.adapters.judges.anthropic:build` | `anthropic` | `/v1/messages` | header key |
| metric | `evalharness.adapters.metrics.judge_pass:build` | - | - | judge-backed correctness, the metric that calls a provider judge |

Both families declare `Capabilities(provider=..., requires_credential=True,
max_attempts=3)`. A judge additionally declares `requires_model=True`, because
the API call needs the spec's declared model.

## Auth shapes

`auth` is an adapter option. The harness owns the key by default; the vault
stores it and the resolved handle arrives in `RunContext.credential` (candidate)
or on `JudgeRequest.credential` (judge). No adapter reads settings.

| `auth` | Header sent | Credential required |
| --- | --- | --- |
| `bearer` (OpenAI-compatible default) | `Authorization: Bearer <secret>` | yes |
| `header` (Anthropic default) | `x-api-key: <secret>` plus `anthropic-version: 2023-06-01` | yes |
| `none` | no auth header | no |

`header` requires `header_name`; the Anthropic factories default it to
`x-api-key`, and `options.header_name` can name a proxy header instead.
The version header is a dialect constant, not a secret; a different API version
becomes a future dialect rather than an option on this one.

`auth: none` is the explicit endpoint-owned case, used by the demo and by
conformance so neither needs a stored secret. A missing credential for
`bearer` or `header` is a spec error before any cell runs, naming
`candidates[i].credential` or `judges[i].credential`:

```text
spec error: judges[0].credential='judge-key': judge 'strict' needs a credential resolved from the vault, but none is stored under that name. Fix: add it with 'evalh credentials add' or use a judge that needs no credential
```

## Request and response mapping

### OpenAI-compatible

Request:

```json
{"model": "gpt-4o-mini",
 "messages": [{"role": "system", "content": "..."},
              {"role": "user", "content": "What is 6 times 7?"}],
 "temperature": 0.0,
 "seed": 7}
```

The system message is omitted when `options.system` is empty. The user content
is the prompt override when the spec selected one, otherwise the case prompt,
followed by every injected context chunk separated by a blank line.

Response: `choices[0].message.content` is the answer or verdict text;
`usage.prompt_tokens` and `usage.completion_tokens` are the reported usage;
`model` is reported on the cell when present.

### Anthropic-style

Request:

```json
{"model": "claude-haiku-4-5",
 "max_tokens": 1024,
 "messages": [{"role": "user", "content": "What is 6 times 7?"}],
 "system": "...",
 "temperature": 0.0}
```

`system` is always sent (empty when the option is empty); `max_tokens`
defaults to 1024 and is an option. There is no `seed` field in this dialect.

Response: `content[0].text` is the answer or verdict text;
`usage.input_tokens` and `usage.output_tokens` are the reported usage.

### Usage

When the provider reports both counts the cell or verdict records
`usage_source=provider` and the numbers as-is. A response missing one count
keeps the reported count and estimates only the missing one with the documented
`ceil(characters / 4)` rule; the source is then `estimated`, so a cost figure
never hides which numbers were billed. Prompt characters include the system
text and the injected chunks.

## Judges

A provider judge asks its declared model for one JSON verdict:

```json
{"passed": true, "reason": "the answer is correct", "score": 1.0}
```

`passed` is the only field that decides the check; `reason` is required by
`judge/3` and defaults to the reply text; `score` is optional. The reply may
arrive in a Markdown code fence. A reply without a JSON object falls back to a
whole-word `PASS` or `FAIL` marker, and any other prose is a **fail** with the
model's text as the reason: an unparseable reply never silently passes a
candidate. Declaration-specific guidance goes in `options.instructions` and is
appended to the system prompt.

The judge declaration's `model` is the model the adapter calls and the price it
is billed at; it must be declared (a spec error names `judges[i].model`
otherwise) and must have a price. The executor builds one `JudgeSession` per
declared judge per cell; the session copies its resolved credential and model
onto the `JudgeRequest` before calling the adapter, so a provider judge
authenticates and names its model without a settings object. Deterministic
judges declare `Capabilities()` and keep working with no credential.

The verdict reports `prompt_tokens`, `completion_tokens`, `usage_source` and
the provider's `model`; `judge/6` publishes that contract. Judge tokens and
cost stay attributed to the cell that triggered the verdict, exactly as in
[judges.md](judges.md).

## Prices

The shipped table is [config/prices.yaml](../src/evalharness/config/prices.yaml),
packaged at `evalharness/config/prices.yaml`. It is **USD per million tokens**,
keyed by provider then model:

```yaml
openai-compatible:
  gpt-4o-mini: {input: 0.15, output: 0.60}
  gpt-4o: {input: 2.50, output: 10.00}
anthropic:
  claude-haiku-4-5: {input: 1.00, output: 5.00}
  claude-sonnet-4-5: {input: 3.00, output: 15.00}
```

These are representative list prices; update them when a provider changes its
rates. A spec overrides one entry at two levels, most specific first:

```yaml
provider_prices:
  openai-compatible:
    gpt-4o-mini: {input: 0.10, output: 0.40}   # wins for that provider
prices:
  gpt-4o-mini: {input: 0.12, output: 0.50}      # model-only fallback
```

Resolution order for a candidate or judge is: `provider_prices[provider][model]`,
then `prices[model]`, then the shipped table for that provider. The
model-only syntax every earlier spec uses keeps working and still wins over a
shipped default; a provider-specific entry wins over both.

A declared model with no price under any of the three is a validation error
before any spend, naming the model:

```text
spec error: prices.not-a-priced-model='not-a-priced-model': no price is declared for this model at provider 'openai-compatible'. Fix: add provider_prices.openai-compatible.not-a-priced-model: {input: <usd_per_mtok>, output: <usd_per_mtok>}, or an entry for 'not-a-priced-model' in the shipped config/prices.yaml
```

The run snapshots the resolved table as version 2, nested by provider, so an old
run keeps the prices it was launched with and the compare view can tell two
price tables apart:

```json
{"version": 2,
 "providers": {"openai-compatible": {"gpt-4o-mini": {"input": 0.15, "output": 0.6, "tool": 0.0}},
               "": {"stub-1": {"input": 0.15, "output": 0.6, "tool": 0.0}}}}
```

The empty provider key is the provider-agnostic bucket a custom endpoint or a
deterministic judge uses. The compare view flattens the snapshot to
`provider/model` keys for display and reads a pre-#17 flat snapshot unchanged.

## Per-provider attribution

Answer and tool cost belong to the provider that ran the candidate. Judge cost
belongs to the provider that served the judge, not to the candidate it judged,
which is what makes "the judge costs more than the answer" visible per provider.

Two additive cell columns carry the evidence:

| Column | Meaning |
| --- | --- |
| `cells.provider` | the candidate's provider for that cell, empty for a provider-agnostic adapter |
| `cells.judge_provider_usage` | JSON: judge prompt/completion tokens and USD per judge provider for that cell |

and one new table stores the run-level roll-up:

| Column | Meaning |
| --- | --- |
| `provider_costs.run_id`, `provider_costs.provider` | primary key |
| `n_cells` | candidate cells that provider ran |
| `prompt_tokens`, `completion_tokens` | candidate tokens |
| `judge_prompt_tokens`, `judge_completion_tokens` | judge tokens served by that provider |
| `answer_cost_usd`, `judge_cost_usd`, `tool_cost_usd` | the cost split |
| `cost_usd` | the sum of the three parts |

A new table rather than more columns on `runs`: a run has a variable number of
providers, and a `provider_1_*`, `provider_2_*` shape would cap it and bloat
every run row. Schema version 5 adds the two nullable cell columns in place and
lets `create_all` create the new table; no row is rewritten.

The rows are recomputed from the run's stored cells when it closes, so a resume
recomputes them from the same cells rather than double-counting. Every row's
`cost_usd` is its three parts, and the rows sum to the run's stored
`cost_usd`. The CLI prints them after the cost split:

```text
cost: answer $0.000090, judge $0.000252, tools $0.000000
provider openai-compatible: cells=1 prompt_tokens=11 completion_tokens=5 judge_tokens=32 cost=$0.000191
provider anthropic: cells=1 prompt_tokens=11 completion_tokens=5 judge_tokens=32 cost=$0.000151
```

## Errors and retries

The classification is identical for both families and for candidates and
judges, because it lives in one place:

| Condition | What happens |
| --- | --- |
| connect error, timeout | `TransportError`; the executor retries |
| HTTP 429 | `TransportError`; the executor retries |
| HTTP 5xx | `TransportError`; the executor retries |
| HTTP 401 or 403 | one request, then `ProviderError` naming the provider's message |
| any other HTTP 4xx | one request, then `ProviderError` naming the provider's message |
| 2xx with a body that is not JSON | one request, then `ProviderError` naming the problem |
| 2xx with a body that does not match the provider shape | one request, then `ProviderError` naming the field |
| 2xx with a valid answer, right or wrong | recorded and scored; never retried |

The attempt budget is the declared `capabilities.max_attempts = 3`: one request
plus two retries, with the executor's 50 ms then 100 ms backoff. A 401 is never
retried. When retries are exhausted the cell is stored with `status=error` and
`error_type=TransportError`; a provider rejection is `error_type=ProviderError`
and `output.error` carries the provider's message verbatim, after redaction, so
a provider that echoes the key does not leak it. A judge that fails while
scoring a cell is recorded the same way: the run never crashes, remaining cells
still execute, the run status becomes `partial`, and the exit code stays 0
unless a gate fails or the budget aborts.

## Running the demo

`examples/provider_server.py` answers both paths with a scripted reply and the
documented JSON verdict, and reports usage in each dialect. It needs no key:

```bash
python examples/provider_server.py          # terminal 1, 127.0.0.1:8770
python -m evalharness run examples/specs/providers.yaml   # terminal 2
```

The spec declares two candidates and two judges, one at each provider, and two
credential names. Both adapters use `auth: none` so no paid key and no stored
secret is needed; four cells run, four verdicts execute, and the run prints two
provider cost lines. To exercise the real auth shapes against the same stub:

```bash
python examples/provider_server.py --openai-token "$OPENAI_KEY" --anthropic-token "$ANTHROPIC_KEY"

evalh credentials add openai-key --provider openai-compatible --secret-env OPENAI_KEY
evalh credentials add anthropic-key --provider anthropic --secret-env ANTHROPIC_KEY
```

then set the candidate and judge options to `auth: bearer` (OpenAI-compatible)
or `auth: header` with `header_name: x-api-key` (Anthropic-style) and name the
stored credentials.

## Conformance

All four adapters are in `adapters/manifest.py` with suite options pointing at
a real stub server, never a mock:

- `EVALH_PROVIDER_CONFORMANCE_BASE_URL` names the stub; unset, the rows use
  `http://127.0.0.1:8770`, the demo server's address.
- CI starts `examples/provider_server.py` and sets the variable before
  `python -m evalharness conformance --all`.
- The test fixture that starts the stub replaces `base_url` with an ephemeral
  port for that run.

Clause identifiers are append-only. The provider adapters pass
`candidate/1`-`candidate/8` and `judge/1`-`judge/6`; the last judge clause,
added here, requires every verdict to report non-negative token counts and a
`usage_source`. Run one adapter directly:

```bash
python -m pytest tests/test_providers.py -q
evalh conformance --adapter evalharness.adapters.candidates.openai_compat:build --kind candidate
```

## What #18 must know

- **A provider is data plus a dialect.** Add a `ProviderDialect` in
  `provider_wire.py` (path, response model, payload builder, reader, static
  headers) and two factories that call `ProviderCandidate` and `ProviderJudge`.
  Do not add a branch to the executor.
- **Judges get ambient state through `JudgeRequest`.** `JudgeSession.verdict`
  copies the resolved `model` and `credential` onto the request. A new judge
  capability belongs in `Capabilities`, and validation enforces
  `requires_model`/`requires_credential` there, not in the adapter.
- **Prices are provider-qualified.** `ResolvedSpec.candidate_prices` and
  `judge_prices` are keyed by name because one model can have two prices at two
  providers. Use those maps, not `ResolvedSpec.prices`.
- **Attribution is per cell.** If a new adapter introduces another place a
  provider spends money, record it in `cells.provider` or
  `cells.judge_provider_usage` so the run-level rebuild stays exact and stays
  resume-safe. The sum invariant is tested: provider rows == run total.
- **Schema is version 5.** The two cell columns are nullable for old databases;
  `provider_costs` is created by `create_all`. Do not renumber the
  conformance clauses; append `candidate/9` or `judge/7` if the contract grows.
