# Read-only console

A run launched from a terminal is legible in a browser: the whole matrix, one
cell, and the raw artifact behind that cell. Pages are Jinja2 templates served
by FastAPI; there is no Node build, no CDN and no client-side state.

The console is read-only. It never launches, cancels or edits a run. The CLI
owns execution; the console reads the same database and artifacts directory.

## Run it

~~~bash
evalh serve                                   # http://127.0.0.1:8000
evalh serve --host 127.0.0.1 --port 8000
evalh serve --db sqlite:///tmp/harness.db --artifacts tmp/artifacts
~~~

`serve` reads the same `EVALH_*` environment as `evalh run`:
`EVALH_HOME`, `EVALH_DATABASE_URL` and `EVALH_ARTIFACTS_DIR`. The two path
flags override the environment for this process only. Uvicorn's access log is
off because its default line includes the query string; the application logs
method, path and status through its own middleware and never logs the query,
headers or bodies.

## Page map

| Page | Route | What it shows |
| --- | --- | --- |
| Runs | `GET /` | Status, spec name, candidates, cells ok, pass rate, cost, duration and start time, newest first. Query parameters `candidate=` and `metric=` narrow the list. |
| Run detail | `GET /runs/{run_id}` | Candidate by case heatmap, aggregates with 95 percent Wilson intervals and pass@k / pass^k, gates, and the selected cell. Add `?cell=<cell_id>` to select one. |
| Cell detail | `GET /runs/{run_id}/cells/{cell_id}` | The same run detail page with that cell selected. |
| Artifact | `GET /runs/{run_id}/cells/{cell_id}/artifact` | The stored `cell.json` for the cell. Add `?file=answer.txt` for the answer file. |
| Not found | any unknown id | A 404 page with a link back to the run list. |

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

## JSON API

All JSON routes return 404 with a JSON detail for an unknown run. Datetimes are
ISO 8601 strings; a missing value is `null`, never a fabricated zero.

| Route | Returns |
| --- | --- |
| `GET /api/runs` | A JSON array of run summaries. Accepts `candidate=` and `metric=`. |
| `GET /api/runs/{run_id}` | One run with summary fields, `aggregates`, `gates` and `cells`. |
| `GET /api/runs/{run_id}/cells` | `{"run_id": ..., "cells": [...]}`; every cell carries its checks. |
| `GET /api/runs/{run_id}/events` | The SSE stream, identical to the HTML-facing `/runs/{run_id}/events`. |

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
  `router`. `create_app` imports every module in that package, so #10 to #13
  add files and do not edit `app.py`.
- **Shared dependencies** live in `api/deps.py`: `get_repository`,
  `get_settings`, `get_templates` and `render`. Reuse them instead of
  reaching into `request.app.state` directly.
- **New read models** belong in `Repository`, the only module that talks SQL.
  The console currently uses `list_run_summaries`, `list_run_filters`,
  `list_cells`, `list_checks_for_run`, `get_cell`, `artifact_path`,
  `list_aggregates`, `list_gates`, `list_events` and
  `list_events_after`.
- **Spec editing and launch** (#10, #11) add write routes and spec validation
  on top of the same repository; the read routes stay read-only.
- **Compare and credentials** (#12, #13) add pages and JSON under the same
  discovery rule. If a new first-party adapter is introduced, add its factory
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

The page also offers a form with two run selects. With no query parameters it
renders an empty state that explains what to pick; with no runs at all it says
how to start one. A missing or unknown run id, an unknown candidate, or a
half-specified candidate query is a 404 with a message naming the argument to
fix. A candidate name must have cells in the run it is paired with.

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
