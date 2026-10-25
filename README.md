# eval-harness

Adapter-based evaluation harness. The harness is the product; pipelines, agents and corpora are candidates behind adapters.

Status: a spec-driven run, an encrypted credential vault, judge-backed metrics, the
public adapter contract suite, two provider families as candidates and judges, and a
console that edits specs, launches, cancels and reads runs, plus a container and compose
file that bring the harness and PostgreSQL up together. One command validates a spec,
expands it into cells, executes them with bounded concurrency, and writes a run with
cells, checks, aggregates with intervals, and artifact paths that read back. A judge
verdict carries a reason, evidence and its own tokens and cost, attributed to the cell
that triggered it, split from the answer's cost and rolled up per provider. Provider
keys are added, listed, verified and deleted from the command line or the console,
encrypted at rest and redacted everywhere. The console serves the run list, the candidate
by case heatmap, per-cell evidence, the spec editor, a side-by-side compare view with
deltas, intervals and cost per correct answer, and launch and cancel, one run per
container at a time.
The spec of record is issue #1 in this repository.

## What it will be

A versioned YAML **spec** declares the source of cases, the **candidates** (each with its own credential, provider, model, prompt variant and context setting), the **metrics** to score, per-model **prices**, and the **gates** that decide pass or fail. A planner expands the spec into **cells**, an executor runs them with bounded concurrency and a budget, a scorer produces per-cell **checks** and per-candidate **aggregates** with intervals, and a console serves the results: heatmap, per-cell evidence, and a compare view with deltas and cost per correct answer.

Credentials are BYOK. One container per person, keys verified on demand, encrypted at rest, masked everywhere.

## Start here

- `docs/design-adapter-harness.md` — the design note: layering, protocols, spec schema, credential and cost model, executor semantics, storage schema, migration phases.
- `docs/spec-v1.md` — the spec schema as implemented, field by field.
- `docs/adapters.md` — the five adapter contracts, clause numbering, the manifest and the conformance command.
- `docs/judges.md` — the judge contract, reference judges, configuration hashing, cost attribution and two judges in one run.
- `docs/credentials.md` — BYOK flow, master key lifecycle, what is stored, redaction guarantees, verification request.
- `docs/console.md` — the page map, JSON API, SSE contract and how to serve a run in a browser.
- `docs/http-candidate.md` — the v1 wire contract for someone else's agent: request, response, auth, retries, error mapping, usage estimation.
- `docs/providers.md` — the two provider families for candidates and judges: matrix, auth shapes, request and response mapping, the JSON verdict, the price table and per-provider cost attribution.
- `contract/candidate-v1.schema.json` — the same contract as JSON Schema (append-compatible only).
- Issue #1 — the spec: problem, solution, 58 user stories, implementation decisions, testing decisions, out of scope.

## Quick start: the stranger's path

This is the whole path from a clean machine to a result: no maintainer, no paid
credential, and no network call in the smoke run. It takes about two minutes.

### 1. Prerequisites

- Docker Engine with Compose v2: `docker --version` and
  `docker compose version` both answer.
- Port 8000 free on the host, or set `EVALH_PORT` in `.env` to another port.
- Nothing else for the container path. The smoke spec needs no provider key.
- The CLI-only path (below) needs Python 3.11 or newer and `pip`, and no
  PostgreSQL. The image targets Linux Python 3.12.

### 2. Bring up the harness and bootstrap the master key

~~~bash
git clone https://github.com/kenmann01/eval-harness.git
cd eval-harness
cp .env.example .env
# Generate the database password and put it in .env as
# EVALH_POSTGRES_PASSWORD=...
python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose build
docker compose run --rm --no-deps harness
~~~

The first start has no master key yet. The harness prints a key exactly once and
exits 2, before it opens the database:

~~~text
no master key is configured; generated one (shown once, not saved):
  EVALH_MASTER_KEY=<44 urlsafe base64 characters>
store it in the environment, then re-run this command.
spec error: EVALH_MASTER_KEY=None: no master key is configured, so the credential vault cannot open. Fix: store the generated key in the environment and re-run this command
~~~

That exit code is the bootstrap, not a crash. Paste the printed value into `.env`
next to `EVALH_MASTER_KEY=` (the file is gitignored), then start the stack for real:

~~~bash
docker compose up -d
docker compose ps                 # db and harness are healthy
curl -s http://127.0.0.1:8000/healthz
~~~

Open the console at <http://127.0.0.1:8000>. A restart with `EVALH_MASTER_KEY`
set serves again, keeps every existing run, and does not print a new key: the key
is only generated when none is configured.

The compose file refuses to start without `EVALH_POSTGRES_PASSWORD` and has no
default secret. PostgreSQL 16 starts first; the harness waits for its healthcheck.

### 3. Add a provider key (optional for the smoke path)

The bundled specs need no key. To point a spec at a real model, store the key in
the encrypted vault; never put it in YAML.

~~~bash
# Interactive: the prompt does not echo the secret.
docker compose exec harness evalh credentials add openai-prod --provider openai

# Non-interactive: pass the host variable into the container.
OPENAI_API_KEY=sk-... docker compose exec -e OPENAI_API_KEY harness \
  evalh credentials add openai-prod --provider openai --secret-env OPENAI_API_KEY

docker compose exec harness evalh credentials list
docker compose exec harness evalh credentials test openai-prod \
  --verify-url https://api.openai.com/v1/models
~~~

The same vault is managed in the console at
<http://127.0.0.1:8000/credentials>: add, test, disable, enable and delete
are form posts, and the page shows metadata and a fingerprint prefix rather
than a secret.

`list` shows name, provider, creation time, last verification and last error,
never the secret. `test` exits 1 on a rejected key. See
[docs/credentials.md](docs/credentials.md).

### 4. Write or copy a spec

A spec is one YAML file in the harness specs directory, `/data/specs` in the
container and on the `harness-data` volume. Write it in the console at
<http://127.0.0.1:8000/specs> (Check validates and estimates before Save writes),
or copy an existing file in:

~~~bash
docker compose cp ./my-spec.yaml harness:/data/specs/my-spec.yaml
~~~

The image seeds `smoke.yaml` and `stub-pack.yaml` into `/data/specs` when the
volume is first created. Copy `smoke.yaml` as a starting point. The field-by-field
reference is [docs/spec-v1.md](docs/spec-v1.md); a spec declares its source,
candidates, metrics, prices and optional gates, and it never contains a secret.

### 5. Run it

From the console, open <http://127.0.0.1:8000/launch>, pick a spec and press
Launch. One run at a time per container; the run page offers Cancel while it is
in flight.

The CLI drives the same orchestration against the same database and artifacts:

~~~bash
docker compose exec harness evalh validate /data/specs/smoke.yaml
docker compose exec harness evalh run /data/specs/smoke.yaml --dry-run
docker compose exec harness evalh run /data/specs/smoke.yaml
docker compose exec harness evalh run /data/specs/stub-pack.yaml
~~~

`smoke.yaml` is two stub candidates over one case; it exits 0 without a
provider key and without network. `stub-pack.yaml` runs all four stub shapes plus a deterministic
judge; the always-failing candidate is recorded as an error and the run still
exits 0.

The same launch over HTTP returns 202 with the run id:

~~~bash
curl -s -X POST http://127.0.0.1:8000/api/runs \
  -H 'content-type: application/json' -d '{"spec": "smoke.yaml"}'
~~~

### 6. Read the results

The run list is <http://127.0.0.1:8000/>. A run page shows the candidate by case
heatmap, every aggregate with its 95 percent Wilson interval and pass@k / pass^k,
the gates, and the selected cell's answer, checks, evidence, tokens, latency and
cost. Artifacts are downloadable from the cell. The compare view at
<http://127.0.0.1:8000/compare> answers whether B beats A, with deltas on the
metric and on cost per correct answer. The JSON API under `/api/runs` serves the
same values. [docs/console.md](docs/console.md) is the page and API reference.

The CLI prints the same table, aggregates and gates:

~~~text
run <run_id> status=ok cells=2 ok=2 cost=$0.000...
~~~

### 7. Smoke the whole path

With the stack serving:

~~~bash
docker compose exec harness python scripts/smoke.py http://127.0.0.1:8000
~~~

The script asserts: `/healthz` is 200 with a reachable database and a writable
artifacts directory; the vault is configured; the spec is valid and uses only
`provider: none` credentials and no network URL; a run launched through
`POST /api/runs` finishes with a status that exits 0; the run reads back through the API
with cells and checks; and the console run list and run page answer. It exits 0
with a short report, or 1 with the fix. It uses no paid credential and makes no
network call outside the harness URL.

`stub-pack.yaml` ends as `status=partial`: one candidate fails on purpose and
the run still exits 0 because its gates pass. The smoke test accepts `partial`
only when every gate passed, which is the same exit-0 contract.

From the host, install httpx and run the same script against the published port:

~~~bash
python -m pip install "httpx>=0.27"
python scripts/smoke.py http://127.0.0.1:8000 --spec smoke.yaml
~~~

If this machine has no Docker daemon, use the equivalent local path in
[CLI-only quick start](#cli-only-quick-start-no-docker) below and point the script
at `http://127.0.0.1:8000`. The repository CI builds the image, runs
`docker compose config`, starts the stack with a generated key and runs this
script inside the harness container. Those steps cannot be proven without a
daemon, so treat a local pass over the in-process server as the honest fallback.

## CLI-only quick start (no Docker)

~~~bash
python -m pip install -e .
export EVALH_HOME="$PWD/.evalh"
export EVALH_SPECS_DIR="$PWD/examples/specs"
# First run prints EVALH_MASTER_KEY=... once and exits 2; rerun with it exported.
python -m evalharness run examples/specs/smoke.yaml
python -m evalharness run examples/specs/stub-pack.yaml
python -m evalharness serve                  # http://127.0.0.1:8000
python -m evalharness conformance --all
# provider demo: two candidates and two judges, two providers, no paid key
python examples/provider_server.py                       # terminal 1
python -m evalharness run examples/specs/providers.yaml  # terminal 2
~~~

`EVALH_HOME` is where the SQLite database and artifacts live by default;
`EVALH_DATABASE_URL` and `EVALH_ARTIFACTS_DIR` override them individually.
<<<<<<< HEAD
The stub-pack demo runs all four candidate stubs (scripted, slow, prompt-fixed
and always-failing) plus the deterministic judge in one spec. It exits 0, records
the always-failing cells as errors, and needs no credentials. The judge example runs
two scripted judges against the same cited answer and prints both verdicts and the
answer/judge/tool cost split. The `evalh conformance --all` command runs the same
public contract suite as CI and prints one line per clause. Its HTTP candidate row
needs a live endpoint: CI starts `examples/http_candidate_server.py` first and
points the row at it with `EVALH_HTTP_CONFORMANCE_BASE_URL`; locally start the
demo server, or set the same variable, or that row reports a connection error. The
four provider rows follow the same rule with `examples/provider_server.py` and
`EVALH_PROVIDER_CONFORMANCE_BASE_URL`.
=======
PostgreSQL is optional: `pip install -e ".[postgres]"` and set
`EVALH_DATABASE_URL=postgresql+psycopg://user:password@host:5432/evalharness`.
The conformance suite's HTTP candidate row needs a live endpoint: CI starts
`examples/http_candidate_server.py` and points it at the row with
`EVALH_HTTP_CONFORMANCE_BASE_URL`; locally start that server or set the same
variable.

## Master key, data and backups

The vault needs a 32-byte Fernet key, written as 44 urlsafe base64 characters.
Resolution order: `EVALH_MASTER_KEY`, then `EVALH_MASTER_KEY_FILE`, then the
generated-key-and-exit-2 bootstrap. Every command that opens the vault
(`run`, `serve` and every `credentials` subcommand) follows it; `validate`
and `run --dry-run` do not need a key.

**One key, one vault.** Losing the key does not lose runs, but every stored
provider key becomes unreadable; delete and re-add each one. Back up `.env` with
the same care as the database.

The two compose volumes are:

| Volume | Mount | Holds |
| --- | --- | --- |
| `harness-data` | `/data` | Artifacts (`/data/artifacts`), specs (`/data/specs`), and the SQLite database (`/data/db/harness.db`) when the PostgreSQL URL is not set |
| `postgres-data` | `/var/lib/postgresql/data` | PostgreSQL 16 data, including every run, cell, check, aggregate and gate |

Back up the database and the artifacts separately:

~~~bash
docker compose exec -T db pg_dump -U evalh evalh > evalh.sql
docker run --rm -v evalharness_harness-data:/data -v "$PWD":/backup busybox \
  tar czf /backup/harness-data.tgz -C /data .
~~~

Restore into a stopped stack:

~~~bash
docker compose down
docker run --rm -v evalharness_harness-data:/data -v "$PWD":/backup busybox \
  sh -c 'rm -rf /data/* && tar xzf /backup/harness-data.tgz -C /data'
docker compose up -d db
docker compose exec -T db psql -U evalh -d evalh < evalh.sql
docker compose up -d
~~~

`docker compose down -v` deletes both volumes and every existing run. Use
`docker compose down` for a restart that keeps them.
>>>>>>> 7e113fb (feat: add an end-to-end smoke script and the stranger quick start)

## Exit codes and gates

Exit codes are the automation contract, defined once in
[`exit_codes.py`](src/evalharness/exit_codes.py):

| Code | Meaning |
| --- | --- |
| 0 | Success: the run finished and no gate failed. A run whose cells errored but whose gates passed still exits 0, and a cancelled run exits 0 unless a gate failed. |
| 1 | Gate failure: the run finished and at least one spec gate failed. A gate failure also wins over cancellation. |
| 2 | Spec error: the spec is invalid or unreadable, nothing was executed. Also the first start without a master key. |
| 3 | Budget abort: the run stopped because spend exceeded `budget_usd`. It wins over a gate. |

A gate is a threshold on an aggregate, evaluated after the run. Omit
`candidate` for the run-wide figure, or name one candidate to gate only that
candidate's aggregate:

~~~yaml
gates:
  - metric: exact_match
    min: 0.90
  - metric: exact_match
    candidate: stub-concise
    max: 0.95
~~~

Each gate result is stored with its metric, candidate (or none for run-wide),
comparison (`>=` or `<=`), threshold, observed value and n, so a red build says
which kind of failure it was. A metric with no samples is undefined, so its
gate fails with `no samples` in the detail. Gate failure always exits 1; there
is no flag to turn it off. See [docs/runs.md](docs/runs.md) for the full
contract and [examples/specs/gated.yaml](examples/specs/gated.yaml) for a spec
that passes its gate without a paid credential.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| The first start (`docker compose run --rm --no-deps harness`) exits 2 with `EVALH_MASTER_KEY=...` | Expected. Copy the key into `.env` and run `docker compose up -d`. Nothing was written. |
| `no master key is configured` on `evalh serve` | Same bootstrap: run once, copy the printed key into the environment or `.env`, run again. A malformed key also exits 2 and names the expected shape. |
| `/healthz` returns 503 with `the database is unreachable` | The database is down or the URL is wrong. Check `docker compose ps` and `docker compose logs db`; wait for the db healthcheck, then restart the harness. Compose refuses an empty `EVALH_POSTGRES_PASSWORD`, so a password that is set in one place but not the other is the usual cause. |
| Port 8000 is already in use | Set `EVALH_PORT=8080` in `.env` (or in front of the command) and run `docker compose up -d`; the console is then <http://127.0.0.1:8080>. For the CLI, `evalh serve --port 8080`. |
| `permission denied` writing `/data` | The image runs as uid 1001. A named volume is created with the right owner; a bind mount must be writable by that uid (`chown -R 1001:1001`). |
| `docker compose up` cannot connect to the Docker daemon | Start Docker Desktop or the engine. Without a daemon, use the CLI-only quick start; the image and compose claims are validated in CI where a daemon exists. |
| `credentials test` exits 1 | The provider rejected the key. `credentials list` shows the redacted `last_error`; re-add the key. |
| A run exits 1 | A gate failed. The run page and the printed gate line name the metric, the observed value and the threshold. |

## Someone else's agent over HTTP

A candidate can be any endpoint that implements the v1 contract. The endpoint
author reads [docs/http-candidate.md](docs/http-candidate.md) and
[`contract/candidate-v1.schema.json`](contract/candidate-v1.schema.json);
nothing else in this repository is needed. Auth is harness-owned by default,
with `auth: none` for an endpoint that holds its own key.

The reference demo needs no key:

~~~bash
# terminal 1
python examples/http_candidate_server.py

# terminal 2
python -m evalharness run examples/specs/http-candidate.yaml
~~~

The bearer path stores the key once and starts the same server with a token:

~~~bash
export HTTP_DEMO_KEY=local-demo-token
python examples/http_candidate_server.py --token "$HTTP_DEMO_KEY"   # terminal 1
evalh credentials add http-demo --provider custom --secret-env HTTP_DEMO_KEY
evalh run examples/specs/http-candidate-bearer.yaml                  # terminal 2
~~~

## Credentials

Provider keys are BYOK, encrypted at rest with the master key, and replaced with
`[redacted:<name>]` in logs, events, artifacts and cell records. The spec names a
credential, never the secret. See [docs/credentials.md](docs/credentials.md) for
the master key state machine, verification contract and rotation.

## Provenance

Split out of `minirag`, which stays a RAG project and becomes the first example candidate. The design note was written against that tree, so its `app/` module map describes `minirag` until extraction lands; nothing here depends on it.
