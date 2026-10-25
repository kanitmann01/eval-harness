# The HTTP candidate contract, version 1

Someone else's agent becomes a candidate by exposing one HTTP endpoint. This
document is the whole integration story: implement `POST /run`, return the JSON
below, and point a spec at your base URL. You do not need to read the harness
source or import any Python package.

The machine-readable version is
[`contract/candidate-v1.schema.json`](../contract/candidate-v1.schema.json).
The reference implementation is
[`examples/http_candidate_server.py`](../examples/http_candidate_server.py).

## Versioning

Version 1 is **append-compatible only**. A later version may add optional
request or response fields. It will never remove a field, rename one, or change
a field's type. A server must ignore request fields it does not know, and the
harness ignores response fields it does not need, so an endpoint and the
harness can be upgraded independently.

## The request

The harness sends `POST {base_url}{path}` with a JSON content type. `path`
defaults to `/run`.

```json
{
  "case_id": "case-1",
  "prompt": "What is 6 times 7?",
  "context": [
    {"chunk_id": "policy-1", "text": "Answer with a bare number."}
  ],
  "prompt_override": null,
  "seed": 7
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `case_id` | string | The case being run. Unique within one spec. |
| `prompt` | string | The case prompt exactly as the source supplied it. |
| `context` | array of chunks | Injected context, in spec order. Empty when the spec injects none. A chunk is `{"chunk_id": string, "text": string}`. |
| `prompt_override` | string or null | The resolved prompt variant, or null when the spec selected none. |
| `seed` | integer | The run seed. The same seed and case should give a deterministic answer if your agent claims determinism. |

A request you can paste into a terminal:

```bash
curl -s http://127.0.0.1:8765/run \
  -H 'Content-Type: application/json' \
  -d '{"case_id":"case-1","prompt":"What is 6 times 7?","context":[{"chunk_id":"policy-1","text":"Answer with a bare number."}],"prompt_override":null,"seed":7}'
```

## The response

Return HTTP 2xx with a JSON object. `answer` is the only required field.

```json
{
  "answer": "42",
  "citations": ["policy-1"],
  "usage": {"prompt_tokens": 812, "completion_tokens": 96},
  "tool_calls": 0,
  "artifacts": {"trace_id": "trace-case-1"},
  "model": "reference-agent-v1"
}
```

| Field | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `answer` | string | yes | none | The answer. A wrong answer is scored, never retried and never an error. |
| `citations` | array of strings | no | `[]` | Chunk ids or URLs the answer relies on. Passed through to the cell unchanged. |
| `usage` | object | no | estimated | Provider token counts, see below. |
| `usage.prompt_tokens` | integer >= 0 | no | estimated | Input tokens the provider billed. |
| `usage.completion_tokens` | integer >= 0 | no | estimated | Output tokens the provider billed. |
| `tool_calls` | integer >= 0 | no | `0` | How many tool calls the agent made for this case. |
| `artifacts` | object | no | `{}` | Opaque JSON recorded on the cell (trace ids, file paths, timings). |
| `model` | string | no | spec model | The model that answered, when it differs from the candidate's spec model. |

Unknown response fields are ignored, so an endpoint may ship extra diagnostics
without waiting for a contract version bump.

## Authentication

The harness owns the credential for the endpoint by default. The spec names a
logical credential, the secret lives in the encrypted vault, and the adapter
sends it from `RunContext.credential`. The adapter never reads settings.

| `auth` option | Header sent | Credential required |
| --- | --- | --- |
| `bearer` (default) | `Authorization: Bearer <secret>` | yes |
| `header` | `<header_name>: <secret>` | yes |
| `none` | nothing | no |

`header` requires `header_name`; validation fails if it is missing.

Store the key once, then name it in the spec:

```bash
evalh credentials add teammate-agent --provider custom --secret-env AGENT_KEY
```

```yaml
candidates:
  - name: teammate-agent
    adapter: evalharness.adapters.candidates.http:build
    credential: teammate-agent
    model: teammate-model
    options:
      base_url: https://agent.internal
      auth: bearer
```

A missing credential for `bearer` or `header` is a spec error **before any
cell runs**, naming the candidate and the field:

```text
spec error: candidates[0].credential='teammate-agent': candidate 'teammate-agent' needs a credential resolved from the vault, but none is stored under that name. Fix: add it with 'evalh credentials add' or set options.auth: none when the endpoint holds its own key
```

### `auth: none`: the endpoint owns its key

Use `auth: none` when the endpoint authenticates the caller itself, for example
behind an internal network or a proxy that injects its own key. No auth header
is sent and the spec may omit `credential` entirely:

```yaml
candidates:
  - name: endpoint-owned-key
    adapter: evalharness.adapters.candidates.http:build
    model: teammate-model
    options:
      base_url: http://127.0.0.1:8765
      auth: none
```

This is the answer to the design note's open question: **the harness owns the
credential by default**, matching the BYOK decision, and `auth: none` is the
explicit escape hatch for an endpoint that holds its own key.

## Usage and the estimation rule

`usage_source` is recorded on the cell:

- `provider` when the response carries both `usage.prompt_tokens` and
  `usage.completion_tokens`. The reported numbers are stored as-is.
- `estimated` when `usage` is missing, or either token count is missing. The
  missing numbers are estimated as **`ceil(characters / 4)`**:
  - prompt characters are the prompt text (the override when present, otherwise
    `prompt`) plus every injected context chunk's text;
  - completion characters are the `answer`.

If one count is reported and the other is missing, the reported count is kept
and only the missing one is estimated. The source is still `estimated`, so a
cost figure never hides which numbers came from the provider. A cost report can
therefore always distinguish billed tokens from guessed ones.

## What the cell record keeps

Everything the endpoint returns is mapped onto the cell, never dropped:

| Response | Cell record |
| --- | --- |
| `answer` | `answer` (and `output.answer`) |
| `citations` | `output.citations`, in the endpoint's order |
| `usage` | `prompt_tokens`, `completion_tokens` and `usage_source` |
| `tool_calls` | `tool_calls` |
| `artifacts` | `output.artifacts`, verbatim |
| `model` | `output.model`, falling back to the candidate's spec model |
| (measured) | `output.latency_ms`, measured by the adapter around the request; `wall_ms` is the executor's total, including queueing and retries |

## Retries and errors

A transport failure is retried; a wrong answer never is.

| Condition | What happens |
| --- | --- |
| Connect error, timeout | raises `TransportError`; executor retries |
| HTTP 429 | raises `TransportError`; executor retries |
| HTTP 5xx | raises `TransportError`; executor retries |
| HTTP 401 or 403 | one request, then a cell error carrying the provider's message |
| Any other HTTP 4xx | one request, then a cell error carrying the provider's message |
| 2xx with invalid JSON | one request, then a cell error naming the JSON problem |
| 2xx with a body that does not match v1 | one request, then a cell error naming the field |
| 2xx with a valid answer, right or wrong | recorded and scored; never retried |

The adapter declares `Capabilities.max_attempts = 3`, so "retry twice" means
**three total attempts**: the first request plus two retries, with a 50 ms then
100 ms backoff. The executor honours the candidate's declared count. Other
adapters keep the older two-attempt default, which is why a flaky candidate
that fails once then answers still passes.

When retries are exhausted, the cell is recorded with `status=error` and
`error_type=TransportError`. When a 4xx arrives, the cell error is
`HttpCandidateError` and `output.error` contains the provider's message. In
both cases the run does not crash: remaining cells still execute, the run
status becomes `partial`, and the CLI exit code is still 0 unless a gate fails
or the budget aborts.

The provider message is read in this order, without rewriting it: JSON
`error` when it is a string, JSON `error.message`, JSON `message`, the plain
body, then the HTTP reason phrase. It passes through the harness redactor, so a
provider that echoes the key in its error text does not leak it.

## Spec options

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `base_url` | string | required | Scheme, host and optional prefix, for example `http://127.0.0.1:8765`. A trailing slash is ignored. |
| `path` | string | `/run` | Appended to `base_url`. |
| `timeout_s` | number | `30` | Per-request timeout in seconds. |
| `auth` | `bearer`, `header`, `none` | `bearer` | Auth shape, see above. |
| `header_name` | string | empty | Header that carries the secret when `auth: header`. Required in that shape. |

`credential` is a candidate-level field, not an option. The adapter is built
from these options only and receives the resolved secret through
`RunContext`.

## Demo

Start the reference server, which answers from a scripted table and needs no
key:

```bash
python examples/http_candidate_server.py
```

In another terminal, run the no-auth spec end to end:

```bash
evalh run examples/specs/http-candidate.yaml
# or: python -m evalharness run examples/specs/http-candidate.yaml
```

The run writes two cells, each with `usage_source=provider`, citations and a
trace id in `artifacts`.

The bearer path, which is harness-owned:

```bash
export HTTP_DEMO_KEY=local-demo-token

# terminal 1
python examples/http_candidate_server.py --token "$HTTP_DEMO_KEY"

# terminal 2
evalh credentials add http-demo --provider custom --secret-env HTTP_DEMO_KEY
evalh run examples/specs/http-candidate-bearer.yaml
```

Without the `credentials add` step the bearer spec fails validation naming
`candidates[0].credential`, before any cell executes.

## Conformance

The HTTP adapter is in the manifest, so it runs the same public candidate
clauses as every other candidate: `candidate/1` through `candidate/8`
(the last one checks the declared retry budget). The suite is driven against a
**real local stub server**, started with the standard library's
`http.server` on an ephemeral port inside the test run. It is not a mock
transport.

The manifest row carries `auth: none` and a `base_url` on the reference
demo server's default loopback port. Start the demo server before
`evalh conformance --all`; without a live process the HTTP clauses fail with a
connection error. The test fixture that starts the stub replaces `base_url`
with an ephemeral port before `run_all()`, and the suite's existing options
mechanism passes it to every clause. To check the adapter yourself:

```bash
python -m pytest tests/test_http_candidate.py -q
```

or, from Python, against any live implementation:

```python
from evalharness.conformance import run_suite

report = run_suite(
    "evalharness.adapters.candidates.http:build",
    "candidate",
    options={"base_url": "http://127.0.0.1:8765", "auth": "none"},
)
assert report.passed
```

## What v1 does not do

Streaming responses, batch requests, per-case credentials, file uploads, signed
requests, or server-push callbacks. Each would be an additive v2 field or a
different adapter, not a change to a v1 field.
