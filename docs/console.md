# Console

A run is legible and launchable in a browser: the whole matrix, one cell, the
raw artifact behind that cell, and a launch and cancel surface. Pages are Jinja2
templates served by FastAPI; there is no Node build, no CDN and no client-side
state.

The results pages never edit a run, a cell or a stored check. Launch and cancel,
described under [Launch and cancel](#launch-and-cancel), are the write actions on
runs; they use the CLI's orchestration, so a console run and a command line run of
one spec produce the same record. The [spec editor](#spec-editor) writes spec
files, the [credentials page](#credentials) writes the encrypted vault and
[calibration](#trust) writes human labels, which annotate a check and never
change it.

This page map describes console v2 and the Trust surfaces (0.3.0): the specs
list, the wizard, the structured edit form, the compare chooser, the decision,
receipt, diff and calibration pages, and the shared error pages.
Every timestamp on these pages is UTC, rendered as `YYYY-MM-DD HH:MM:SS UTC`, and
money is formatted for reading; see
[design-system.md](design-system.md#timestamps-and-money).

## Run it

~~~bash
evalh serve                                   # http://127.0.0.1:8000
evalh serve --host 127.0.0.1 --port 8000
evalh serve --db sqlite:///tmp/harness.db --artifacts tmp/artifacts
~~~

`serve` reads the same `EVALH_*` environment as `evalh run`:
`EVALH_HOME`, `EVALH_DATABASE_URL`, `EVALH_ARTIFACTS_DIR` and the vault's
`EVALH_MASTER_KEY`. It opens the vault before it binds the port, so a first
start without a key prints one once and exits 2, exactly like `run`; the
container bootstrap relies on that. The two path flags override the environment
for this process only. `GET /healthz` and `GET /api/health` report the
harness version, database reachability, artifacts writability and whether the
vault is configured, never the key. Uvicorn's access log is off because its
default line includes the query string; the application logs method, path and
status through its own middleware and never logs the query, headers or bodies.

## Page map

| Page | Route | What it shows |
| --- | --- | --- |
| Runs | `GET /` | Status, spec name, candidates, cells ok, pass rate, cost, duration and start time, newest first. Query parameters `candidate=` and `metric=` narrow the list. |
| Run detail | `GET /runs/{run_id}` | Candidate by case heatmap, aggregates with 95 percent Wilson intervals and pass@k / pass^k, gates, the spec graph and the selected cell. Add `?cell=<cell_id>` to select one. |
| Cell detail | `GET /runs/{run_id}/cells/{cell_id}` | The same run detail page with that cell selected. |
| Artifact | `GET /runs/{run_id}/cells/{cell_id}/artifact` | The stored `cell.json` for the cell. Add `?file=answer.txt` for the answer file. |
| Specs | `GET /specs` | Every spec in the specs directory: name, candidates, cells, estimate, size, modified time, validation state and the Launch, Edit, YAML and Duplicate actions. `/comparisons` redirects here. |
| Raw YAML editor | `GET` and `POST /specs/{name}/edit` | The textarea, Check and Save described under [Spec editor](#spec-editor). An invalid document is never written. |
| Comparison wizard | `GET` and `POST /comparisons/new`, then `/comparisons/new/{step}` for steps 1 to 4 | Four stateless steps that build a new spec from scratch and write it at step 4. |
| Structured edit | `GET` and `POST /comparisons/{name}/edit` | Every section of an existing spec on one page, with Save, Save and run, View YAML, a sticky estimate rail and a 409 when the file changed on disk. |
| Launch | `GET` and `POST /launch` | Spec picker, estimate, budget override and Launch. See [Launch and cancel](#launch-and-cancel). |
| Compare | `GET /compare` | The chooser when no query is given, otherwise the side-by-side comparison with the spec diff above the metrics. See [Compare](#compare). |
| Decision | `GET /runs/{run_id}/decision` | Ranked candidates with checks, pass rate and interval, cost per correct answer, p50/p95 latency, cells, dominance and a Pareto scatter. |
| Receipt | `GET /runs/{run_id}/receipt` | Spec hash, harness revision, operator, seed, timestamps, candidates, judge pins, price snapshot, gates and the diff to the previous run. |
| Spec diff | `GET /runs/{run_id}/diff?against={run_id}` | Structured changes from the baseline run to this one, plus judge drift and a like-for-like refusal. |
| Calibration | `GET /runs/{run_id}/calibration` | Human labels per stored check, agreement and interval, per-judge stamps and the disagreements. |
| Credentials | `GET` and `POST /credentials` | Every stored credential, masked: name, provider, fingerprint prefix, created and last verified times, last error, state and actions. |
| Not found | any unknown id or path | A 404 page that names what was asked and links back to the run list. |
| Refused | a cancel this console does not drive, a changed spec, a missing vault key | A 409 or 503 page with the reason and the next action, never a stack trace. |

Navigation carries `Runs`, `Specs`, `Compare`, `Launch` and `Credentials`, plus a
`New comparison` primary action in the top bar; a run page links to `Compare this
run` and a spec row links to `Launch`. The active section carries
`aria-current="page"`.

Every displayed timestamp is UTC and uses `YYYY-MM-DD HH:MM:SS UTC`; the database
stores timezone-aware UTC values and no page converts to the visitor's zone, so
two machines reading one run agree. Money and percentages are formatted for
reading; see [design-system.md](design-system.md#timestamps-and-money).

The heatmap has one link per planned cell. Colour is never the only signal:
pass is a green check, fail a red cross, error an amber exclamation with a
stripe pattern, cancelled a grey dash, and a recorded cell with no checks a
blue question mark. Each cell is a keyboard-reachable link with a tooltip and
an accessible label; the legend repeats the glyphs.

The selected cell panel shows:

- the answer, or the recorded error when the candidate failed;
- citations, when the candidate returned any;
- every check with its pass/fail state, reason, score, evidence JSON and judge
  configuration hash when present;
- prompt tokens, completion tokens, tool calls, cost, wall latency and the
  usage source (`provider` or `estimated`);
- links to the raw `cell.json` and the answer file.

Artifact paths come from the database, never from the request. The route only
serves `cell.json` or `answer.txt`, resolves the stored directory, and
refuses any file outside the configured artifacts root or any other name. A
missing file is a 404, as is a traversal attempt.

## Spec editor

The spec editor is the console's write surface for spec files: it edits,
validates and saves YAML spec files under `EVALH_SPECS_DIR` (default `specs`
beside the database; `EVALH_HOME/specs` when the database has no file). It writes no
harness state to the database, so the file is the only source of truth and a
run reads exactly the text the editor saved. Runs and artifacts stay read-only;
credentials are managed on the [credentials page](#credentials).

| Route | What it does |
| --- | --- |
| `GET /specs` | Every `*.yaml` file with its parsed name, candidates, cell count, size, modified time and current validation state. |
| `GET /specs/{name}/edit` | A textarea with the raw YAML, a Check button, inline errors under the field they name, and a summary panel with cells per candidate and the cost estimate. |
| `POST /specs/{name}/edit` | Form body `yaml=` plus `action=check` or `action=save`. Check re-renders the editor with the errors and the estimate; Save validates, writes the file atomically and redirects to `/specs?saved={name}`. An invalid spec is never written. |
| `POST /specs/{name}/duplicate` | Copies the file to the first free `<name>-copy`, `<name>-copy-2`, ... name and opens the copy in the editor. |
| `GET /api/specs` | JSON array of the list rows, including `valid` and the first `error`. |
| `GET /api/specs/{name}` | Raw `yaml`, the parsed `summary`, the current estimate and any errors. |
| `POST /api/specs/validate` | Body is the YAML text, either a `text/plain` body or `{"yaml": "..."}` as JSON. 200 with `cells`, `cells_per_candidate`, `cases`, `cost_estimate`, `warnings` and an empty `errors`; 400 with the same shape and a field-named `errors` list. |
| `PUT /api/specs/{name}` | Validates then writes atomically and returns `created: true` for a new file; 400 and no write when the document is invalid. |
| `POST /api/specs/{name}/duplicate` | 201 with the new `name`, `path` and `duplicate_of`; 404 when the source is missing. |

A name is a slug (`[a-z0-9][a-z0-9._-]*`) and the file is always
`<name>.yaml`. The name and the resolved path are both checked, so a traversal
(`../x`) or an absolute path is refused with an error naming `name`. The list
shows only files whose stem is a valid slug, because no other name is
addressable. The read routes accept `name` with or without the `.yaml` suffix;
a write creates the file, so the editor starts from an existing spec or from
`POST /api/specs/{name}/duplicate`.

Validation calls `core.spec.validate_yaml_text`, the same loader behind
`evalh validate`, so a field error reads identically in both places: the
`field`, the rejected `value`, the `reason` and the `fix`. The estimate reuses
the planner (`expand`, `per_candidate_counts`) and
`core.cost.estimate_cost`, with the observed medians of an earlier run of the
same spec when one exists and the 500 prompt / 200 completion token fallback
otherwise. `cost_usd` therefore matches `evalh run --dry-run` for a spec that
has not run yet. The editor also reads the vault's credential states, so a
spec that names a disabled or never-tested credential is refused with the
credential field and the fix; see [the editor rule](#the-editor-rule).

The page works without JavaScript: Check and Save are ordinary submit buttons,
and the server re-renders the same error list and summary. A small debounced
script on top posts the textarea to `/api/specs/validate` and updates the
errors, cells per candidate, cost and the Save button, but it never replaces
the form path.

## Comparisons: the wizard and the structured edit form

`GET /comparisons` is an alias: it redirects to `/specs`, so a saved spec has one
canonical list URL. The wizard writes a new spec; the structured form edits an
existing one; the raw YAML editor above remains the escape hatch for both.

### Wizard

`GET /comparisons/new` redirects to step 1. Steps 1 to 4 live at
`/comparisons/new/{step}` and cover what to vary (name, description, cases),
contexts, prompts and credentials, candidates, then cost and rules (budget,
metrics, judges, gates and prices).

| Route | What it does |
| --- | --- |
| `GET /comparisons/new` | 303 to `/comparisons/new/1`. |
| `GET /comparisons/new/{step}` | Renders that step with the current draft and the estimate rail. |
| `POST /comparisons/new/{step}` | Re-validates the hidden draft, then advances (`Continue`) or, at step 4, writes `<name>.yaml` atomically and redirects to `/comparisons?saved=<stem>` (303 to `/specs`). |

The wizard is stateless: the draft travels in a hidden field, every step
re-validates it, nothing is written until step 4, and a failed step keeps every
value. There is one primary action per step (`Continue`, then `Create
comparison`) plus Back and Cancel. Editing fields without JavaScript still
works because every step is a normal form POST.

### Structured edit form

`GET /comparisons/{name}/edit` renders the whole spec on one page in four
sections: identity and cases, contexts and credentials, candidates, and cost and
rules. Rows are added and removed with ordinary submit buttons, and the sticky
rail repeats the cell count and estimate.

| Action | Result |
| --- | --- |
| `Save` | Validates, writes the file atomically and 303s to `/specs?saved=<stem>`. |
| `Save and run` | Does the same write, then 303s to `/launch?spec=<stem>`. |
| `View YAML` | Opens `/specs/{name}/edit` with the saved text. |
| `Cancel` | Returns to `/specs` without writing. |

The form carries the sha256 digest of the file it rendered. If the file changed
on disk since then, Save is refused with a 409 page that says which version won,
so a terminal edit is never silently overwritten. Invalid input re-renders the
form with a 400, the field named and the value kept.

## Credentials

The credentials page is the browser surface for the vault described in
[credentials.md](credentials.md). A key is added, tested, disabled, enabled and
deleted without a terminal, and the page is safe to screenshot: it renders a
name, a provider, a non-reversible fingerprint prefix, the created and last
verified times, the last redacted error and a state, never a secret and never
the ciphertext.

| Route | What it does |
| --- | --- |
| `GET /credentials` | Every stored credential with its state and actions, plus the add form. |
| `POST /credentials` | The no-JS add form: `name`, `provider`, `secret`. Encrypts, then redirects (303) to `/credentials?added={name}`. The redirect carries the name only. |
| `POST /credentials/{name}/test` | One provider request, then redirects (303) to `/credentials?tested={name}&ok=1` for a pass or `ok=0` for a failure. The provider message is stored redacted and read back from the row. |
| `POST /credentials/{name}/disable` | Switches the credential off and redirects (303). |
| `POST /credentials/{name}/enable` | Switches it back on (303). |
| `POST /credentials/{name}/delete` | Deletes the row and its ciphertext (303). |
| `GET /api/credentials` | JSON array of the masked rows. |
| `POST /api/credentials` | JSON `{"name", "provider", "secret"}`; 201 with the masked row, 400 with a field-named error, 409 for an existing name. |
| `POST /api/credentials/{name}/test` | 200 with `verified`, the redacted `message` and the updated `credential`; 404 for an unknown name, 400 when no verify URL is set. |
| `POST /api/credentials/{name}/enable` and `/disable` | 200 with the updated masked row. |
| `DELETE /api/credentials/{name}` | 200 with `{"deleted": name}`. |

The provider choices are the verifier's auth table, so the form offers exactly
the shapes `adapters/verify/http_verifier.py` knows: `openai` (Bearer token),
`anthropic` (`x-api-key`), `azure-openai` (`api-key`) and `none` (no auth
header). A provider outside that table is refused with a `provider` error
naming the choices; the CLI still accepts any provider and treats it as bearer.

### Masking and states

A row carries `name`, `provider`, `fingerprint`, `created_at`,
`last_ok_at`, `last_error`, `disabled`, `state` and `usable`. The
fingerprint is `Vault.fingerprint`: a truncated SHA-256 of the decrypted secret
(`sha256:` plus sixteen hex characters). It is not reversible, and two
credentials holding the same secret share it. The page shows `-` when the
stored ciphertext cannot be decrypted with the current master key. Neither the
secret nor the Fernet token appears in any HTML response, JSON response,
redirect `Location`, log line or error page.

The state comes from stored metadata, never from the request:

| State | Meaning | Editor |
| --- | --- | --- |
| `verified` | A test passed and no later test failed. | selectable |
| `unverified` | No test has ever passed. | refused |
| `disabled` | The disable switch is on, whatever the test history. | refused |
| `error` | A test passed earlier and a later one failed; the last error is shown. | selectable |

`usable` is true for `verified` and `error`, because both carry a successful
test. The page works without JavaScript: add, test, disable, enable and delete
are ordinary form POSTs that redirect to the masked list. Testing with no
`EVALH_VERIFY_URL` renders a field error naming the variable; the CLI's
`--verify-url` stays the per-call override.

A missing or malformed master key renders a 503 page naming
`EVALH_MASTER_KEY`, its reason and its fix. A key generated by that failure is
printed to the terminal only, never to the page.

The canary test
`tests/test_api_credentials.py::test_the_canary_never_surfaces_anywhere`
submits a canary through the form and the JSON API, makes a stub provider echo
it in a rejection message, and asserts it is absent from every HTML and JSON
body, every redirect, the captured log output, the raw SQLite file and the
stored row, whose `last_error` reads `[redacted:<name>]`. The provider
message goes through the same `SecretRedactor` a run uses.

### The editor rule

The spec editor lists every stored credential with its state and marks a
`disabled` or `unverified` one as not selectable. The rule lives in
`core.spec.validate_spec`, the shared validator: when the console passes the
vault's states, a candidate or judge that names a disabled credential fails
with that field (`candidates[i].credential` or `judges[i].credential`), the
name and an `enable it at /credentials` fix; a never-tested credential fails
with a `test it at /credentials` fix. `POST /api/specs/validate` and the Save
button therefore refuse the same document, so a spec cannot smuggle one in by
posting the API directly. A name that is only declared in the spec and is not
stored keeps its previous behaviour for an adapter that needs no secret.

The CLI reads the same states for the disabled rule, so `evalh validate` and
`evalh run` refuse a disabled credential too. It does not require a test
first: the issue #6 contract is unchanged, so an unverified credential still
validates and a run surfaces the provider's rejection at call time. The console
is stricter because the browser is where a key is chosen.

## JSON API

All JSON routes return 404 with a JSON detail for an unknown run. Datetimes are
ISO 8601 strings; a missing value is `null`, never a fabricated zero.

| Route | Returns |
| --- | --- |
| `GET /api/runs` | A JSON array of run summaries. Accepts `candidate=` and `metric=`. |
| `GET /api/runs/{run_id}` | One run with summary fields, `aggregates`, `gates` and `cells`. |
| `GET /api/runs/{run_id}/cells` | `{"run_id": ..., "cells": [...]}`; every cell carries its checks. |
| `GET /api/runs/{run_id}/events` | The SSE stream, identical to the HTML-facing `/runs/{run_id}/events`. |
| `GET /api/runs/{run_id}/decision` | The ranked decision report and its Pareto points. |
| `GET /api/runs/{run_id}/receipt` | The receipt: identity, candidates, judge pins, price snapshot, gates and the previous-run diff. |
| `GET /api/runs/{run_id}/calibration` | Agreement, per-judge configuration and the labelled checks. |
| `POST /runs/{run_id}/calibration/labels` | Save or replace one human label; `POST .../labels/clear` removes it. Both redirect 303. |

A run summary has `run_id`, `spec_name`, `status`, `cost_usd`,
`pass_rate`, `duration_s`, `n_cells`, `n_ok`, `started_at`,
`finished_at`, `candidates` and `metrics`. The pass rate is passed checks
over recorded checks; it is `null` when the run recorded no checks.

A cell has `cell_id`, `candidate`, `case_id`, `replica`, `status`,
`answer`, `citations`, `prompt_tokens`, `completion_tokens`,
`tool_calls`, `cost_usd`, `wall_ms`, `usage_source`, `error`,
`checks` and `artifact_url`. A check is the stored metric name, passed flag,
score, reason, evidence and judge configuration hash.

The numbers are the same values the CLI prints: `value` is `null` when
`n` is zero, intervals are the 95 percent Wilson bounds, and `pass_at_k` /
`pass_k` are `null` unless the spec ran more than one replica. See
[statistics.md](statistics.md).

## SSE contract

`GET /runs/{run_id}/events` and `GET /api/runs/{run_id}/events` stream the
append-only events table:

~~~text
id: 4
event: cell_completed
data: {"kind": "cell_completed", "payload": {...}, "run_id": "...", "seq": 4, "ts": "..."}

~~~

- The stream polls the events table every 200 ms. It holds no in-process state,
  so a run started by another process is visible to the server immediately.
- `id:` is the per-run sequence number. `event:` is the event kind. `data:`
  is the whole event as JSON, including `seq`, `ts`, `kind` and the
  redacted `payload`.
- Resume from a sequence number with `?after=<seq>` or the standard
  `Last-Event-ID` request header. A valid header wins over the query
  parameter; a non-numeric header falls back to `after`, which defaults to 0.
- The stream ends after the terminal `run_finished` event. A finished run
  therefore replays every stored event in order and closes. A running run
  emits each new event as it is appended.
- A run whose id is unknown is a 404 before the stream starts.

The detail page opens an `EventSource` for this route and closes it on
`run_finished`. The page also renders the stored event history in a
`<details>` element for readers without JavaScript.

## Launch and cancel

A saved spec can be started and stopped from the browser. Both actions call the
same orchestration as `evalh run` (`src/evalharness/service.py`): the load and
validation, the budget estimate and override, the planning, the executor with
the vault, redactor, sink and repository, the aggregates and the gates. A
console launch and a command line launch of one spec therefore record the same
status, cell ids, checks, cost, aggregates and gates, differing only in the run
id and the timestamps.

| Route | What it does |
| --- | --- |
| `GET /launch` | The launch page: spec picker, the estimate for the selected spec, a budget override field and a Launch button. `?spec=<name>` loads an estimate. |
| `POST /launch` | The page form. Launches, then redirects (303) to `/runs/{run_id}`. A refusal re-renders the page with the reason and a 400 or 409 status. |
| `POST /api/runs` | JSON launch: `{"spec": "<name-or-path>", "budget_override_usd": 1.5}` returns 202 with `{"run_id": "...", "url": "/runs/..."}`. |
| `POST /api/runs/{run_id}/cancel` | Sets the in-flight run's cancel token; 200 with `{"run_id", "cancelling": true}`. 409 when this console is not driving that run. |
| `POST /runs/{run_id}/cancel` | The run page's Cancel button. 303 back to the run when it accepted; a 409 page explaining the refusal otherwise. |

### Specs directory

The console launches from `Settings.specs_dir`, the same directory the
[spec editor](#spec-editor) writes. `EVALH_SPECS_DIR` overrides it; the default
is `specs` beside the database, or `EVALH_HOME/specs` when the database has no
file. The picker lists `*.yaml` and `*.yml` files there, and a bare name
resolves to `<name>.yaml` then `<name>.yml`. A path that resolves outside the
directory is refused with 400, so the console only reads what an operator put
there. When the directory is missing or empty the page explains that and falls
back to a plain text field.

### What a launch does

- The spec is loaded, validated, planned and estimated exactly as `evalh run`
  does, including the shared refusal of a disabled credential. The estimate
  uses the medians an earlier run of the same spec measured,
  falling back to 500 prompt and 200 completion tokens per cell. Showing the
  estimate loads the spec, so a broken spec is named on the page before anyone
  presses Launch.
- `budget_override_usd` replaces the cap for this run only and is stored in
  `runs.budget_override_usd` with a `budget_overridden` event, exactly like
  `--override-budget`. An override that is not a finite positive number is a
  400. A spec whose estimate exceeds its cap is refused with 400 and nothing is
  recorded, matching exit code 2 on the command line.
- The run executes in a daemon thread with its own asyncio loop. It creates the
  run row first, then writes cells and events as they finish, so
  `GET /runs/{run_id}` and its SSE stream show progress while the run is still
  in flight.
- `POST /api/runs` answers 202 only once the run row exists, so the returned
  `url` always lands on a page that renders. A launch that fails before that
  (invalid spec, over-budget estimate, a sink that cannot be built) answers 400
  with the reason.

### One run at a time

The console process holds one launch lock in `app.state.launches`, created in
`api/launch.py`. A second launch while one is in flight is refused with 409 and
a message naming the running run id and its cancel route; it is never queued.
The lock is released when the run finishes, when it is cancelled (once the
current cells drain), and on every error path, including a crashed run loop.
A finished or cancelled console run does not block the next launch.

### Cancel

The cancel token contract is unchanged from [runs.md](runs.md#cancel): the token
is honoured between cells, work already in flight finishes and is recorded,
unstarted cells are stored as `cancelled` with the reason, completed cells and
their checks are kept, and the run closes as `cancelled` with `run_cancelled`
before `run_finished`. The console cancels with the reason
`cancelled from the console`, so a cell record says where the stop came from.

Cancelling a run this console is not driving is a 409, including a run started
by a terminal process and a run that already finished. The Cancel button on a
run page is rendered by the shared navigation partial while the stored status is
`running`; pressing it on a run another process owns shows the refusal page
rather than pretending to stop it. The JSON route is the scriptable form.

### Shutdown

The route module registers a shutdown handler. When the application stops
(uvicorn on Ctrl-C, or a test that closes the application) it cancels an
in-flight launch and joins its thread with a ten second timeout, so a run is
left cancelled rather than `running` and a test does not leak a thread.

The shared layout carries the primary navigation (`Runs`, `Specs`, `Compare`,
`Launch`, `Credentials`, and the `New comparison` action) and, when the page context has a
`run` whose status is `running`, the cancel form; `api/routes/launch.py` is one
more discovered route module.

## How tests build the app

`create_app(settings=None, repository=None)` returns the FastAPI application
without binding a port:

~~~python
from evalharness.api.app import create_app
from evalharness.core.config import Settings

app = create_app(settings=Settings.from_env())          # opens the CLI database
app = create_app(settings=settings, repository=repo)    # reuse a test repository
~~~

When `repository` is absent the factory opens `settings.database_url`,
runs schema creation and stores the `Database` on `app.state.database` so
the caller can dispose it. When a repository is supplied the app owns no
database. `app.state` also carries `settings`, `repository` and the Jinja2
`templates` environment.

Tests use `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` and
seed real runs with `evalharness.cli.main(["run", spec, "--db", ...,
"--artifacts", ...])`. No server is started. The route modules are discovered
at application construction time, so a test never imports a route table by
hand.

## Extension points for the next tickets

- **A new page or API surface** is a new module in `api/routes/` exposing
  `router`. `create_app` imports every module in that package, so a new screen
  is a new file and does not edit `app.py`.
- **Shared dependencies** live in `api/deps.py`: `get_repository`,
  `get_settings`, `get_templates` and `render`. Reuse them instead of
  reaching into `request.app.state` directly.
- **New read models** belong in `Repository`, the only module that talks SQL.
  The console currently uses `list_run_summaries`, `list_run_filters`,
  `list_cells`, `list_checks_for_run`, `get_cell`, `artifact_path`,
  `list_aggregates`, `list_gates`, `list_events` and
  `list_events_after`.
- **Spec editing, the wizard and launch** are write routes on top of the same
  repository: they validate through `core.spec`, write the file atomically and
  launch through `service.py`. The read routes stay read-only.
- **Compare and credentials** are pages and JSON under the same discovery rule.
  If a new first-party adapter is introduced, add its factory
  path and kind to `adapters/manifest.py` so `evalh conformance --all`
  covers it.
- **New display formatting** belongs in `api/templating.py` as a Jinja filter,
  and new JSON shapes belong in `api/views.py`, so pages and API stay in step.

## Compare

`GET /compare` answers the decision rather than drawing a chart: does B
beat A, by how much, on how many samples, at what cost per correct answer, and
under which judge. The same numbers are served as JSON at
`GET /api/compare`.

| Query form | Meaning |
| --- | --- |
| `/compare?a=<run id>&b=<run id>` | Compare two whole runs. Each side pools every candidate in that run. |
| `/compare?run=<run id>&candidate_a=<name>&candidate_b=<name>` | Compare two candidates inside one run. |
| `/compare?a=<run id>&b=<run id>&candidate_a=<name>&candidate_b=<name>` | Compare one candidate from each of two runs. |
| `/api/compare` with any of the above | The same comparison as JSON. |

With no query parameters `/compare` is the chooser: a form that compares two
whole runs, plus the candidate pair fields when one run holds two candidates.
The chooser explains what to pick, and with no runs at all it says how to start
one. A missing or unknown run id, an unknown candidate, or a half-specified
candidate query is a 404 with a message naming the argument to fix. A candidate
name must have cells in the run it is paired with.

The JSON shape is:

~~~json
{
  "mode": "runs",
  "a": {"run_id": "...", "spec_name": "...", "status": "ok",
        "candidate": null, "cost_usd": 0.0001, "cost_per_correct": 0.000025,
        "n_cells": 4, "n_ok": 4, "n_checks": 4, "n_passed": 4,
        "candidates": ["stub-concise"],
        "price_table": {"id": "<sha256>", "prices": {"stub-1": {"input": 0.15, "output": 0.6, "tool": 0.0}}}},
  "b": {"...": "same fields"},
  "same_price_table": true,
  "metrics": [
    {"metric": "exact_match",
     "a": {"n": 4, "passed": 4, "value": 1.0, "ci_low": 0.5101, "ci_high": 1.0,
           "cost_per_correct": 0.000025,
           "judge": {"name": null, "adapter": null, "model": null,
                     "config_hash": "<sha256>", "hash_source": "spec"}},
     "b": {"...": "same fields"},
     "delta": -1.0, "interval_low": -1.0, "interval_high": -0.18,
     "verdict": "worse", "reason": "B is lower and the interval excludes zero",
     "comparable": true, "different_judge": false}
  ],
  "one_sided_cases": [
    {"case_id": "case-2", "side": "a", "count": 2,
     "cells": [{"cell_id": "ab12", "run_id": "...", "status": "ok",
                "url": "/runs/.../cells/ab12"}]}
  ],
  "comparable_metrics": ["exact_match"],
  "cross_judge_metrics": [],
  "one_sided_metrics": []
}
~~~

`a` is the baseline and `b` the candidate, so the delta is always B minus A.
Each metric row carries n, passed checks, value and the per-side Wilson
interval, plus the delta, the 95 percent interval on the delta, a verdict, the
judge identity on each side and the cost per correct answer. Cost per correct
answer is the side's total cost divided by that metric's passed checks; it is
`null` when nothing passed. Price table identity is the sha256 of the run's
resolved price snapshot, and `same_price_table` is false when the two sides
used different prices.

The judge is named from the run's spec snapshot, with the adapter and model,
and its `config_hash` is the stored check hash when one exists; otherwise it
is the scorer's configuration hash of the snapshot judge and `hash_source`
says `spec`. When the two
sides' hashes differ for the same metric, `different_judge` is true, the
page shows a prominent warning, and the verdict is `inconclusive`: a
cross-judge difference is never presented as a win.

The union of case ids decides which cases exist. Every case recorded on only
one side is listed with its side, cell count and a link to each cell. It is
never silently dropped from the denominator and never counted as a tie.

The verdict follows the statistical rule in [statistics.md](statistics.md):
`better` when the delta is positive and the interval lies above zero, `worse`
when it is negative and the interval lies below zero, `inconclusive` when the
interval crosses zero, and `undefined` when either side has no samples. A side
with n = 0 is rendered as undefined, never as zero.

## Trust

The Trust surfaces answer whether a result is believable. Each has an HTML page,
a JSON route where one is listed above, and a link from the run page.

### Decision

`GET /runs/{run_id}/decision` ranks every candidate in one run. The pass rate
pools every check the candidate's cells recorded, so a metric that records two
checks per cell contributes twice; the table says `n` beside the rate. Cost per
correct answer is the candidate's whole recorded cost divided by its passing
checks, and is undefined when nothing passed. p50 and p95 latency use ok cells
only, because an errored cell never measured an answer.

Dominance is deliberately conservative. A candidate dominates another only when
its Wilson lower bound is above the other's upper bound, its cost per correct
answer is no higher, its p95 latency is no higher, and all three measures are
known. A candidate with an undefined rate or cost is drawn as undefined, never as
zero, so it cannot reach the frontier. Rank order uses the pass rate first, then
cost per correct answer, then p95 latency, then name; the table states the order
and the scatter plots every candidate with a defined rate and cost, filled for the
frontier and hollow for the dominated.

### Receipt and spec diff

`GET /runs/{run_id}/receipt` is the provenance record for one run: run and spec
ids, the stored spec hash, the harness revision (`EVALH_HARNESS_SHA` when the
build sets one, otherwise the package version), the operator recorded from
`EVALH_OPERATOR`, seed, UTC timestamps, candidate adapters and models, judge pins
with configuration hashes and last use, the price snapshot fingerprint and rates,
gate results and the diff to the previous run.

`GET /runs/{run_id}/diff?against={baseline_id}` compares the snapshots behind two
runs. Changes are grouped into cases, candidates, judges, prices, run fields and
gates and ordered by category then path. A field the baseline never declared
reads `absent`. A judge configuration change on the same metric is judge drift:
the page names it and the comparison is flagged as not like-for-like until the
judge is pinned. Missing `?against` and an unknown run are 404 with the argument to fix.

### Calibration

`GET /runs/{run_id}/calibration` shows a human label per stored check. Saving a
label writes to the `human_labels` table with the run id, cell id and metric; it
never changes the check, so agreement can always be recomputed. The summary
reports labelled and unlabelled counts, the agreement rate with its 95 percent
Wilson interval, and the last human label time. One row per judge configuration
carries its configuration hash and last-validated stamp, and the disagreement
table links each mismatched check to its cell. `POST .../labels` saves or replaces
one label and `POST .../labels/clear` is an idempotent delete; both redirect 303.

### Sample-size CLI

Before a run, `evalh sample-size --baseline 0.6 --effect 0.2` prints the samples
per side needed for a two-proportion test at 80 percent power and a two-sided
alpha of 0.05. `--power`, `--alpha` and `--json` cover tighter plans and
automation. A baseline outside 0 to 1, an effect that leaves the feasible range,
or a power or alpha outside 0 to 1 exits 2 and names the argument, the value and
the fix. The same calculation supplies the what-would-change-my-mind line under
the compare verdict.
