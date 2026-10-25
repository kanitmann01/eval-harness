# The minirag parity fixture

This directory is a verbatim copy of the parity fixture that used to guard the
three-arm scoreboard in [kenmann01/minirag](https://github.com/kenmann01/minirag).
Issue #16 removed scoring from that repository, so the drift guard moved here,
next to the scorer it guards.

- Source repository: `kenmann01/minirag`
- Source branch: `feat/candidate-parity`
- Source commit: `10e2103` (the fixture itself was added in `37209f8`)
- Copied: 2026-10-25, no content changes

| File | What it holds |
| --- | --- |
| `graph.json` | The four-loan graph the task generator read. |
| `tasks.json` | Four tier-1 and four tier-2 tasks in the generator's `Task` shape. |
| `retrieval.json` | The canned bridge payload for each tier-2 prompt. |
| `answers.json` | The canned answer table, model usage, judge usage, seed and model name. |
| `expected.json` | The per-model price table and the expected three-arm scoreboard. |
| `harness-spec.yaml` | The two-command demo spec that pointed the harness at the live minirag candidate endpoint. |

`expected.json` is pinned by sha256:

```
sha256(expected.json) = 7f5fc7b46aab954e5f1d51ab466602d324934eb0c1b3dc55f9147a450e0572c7
```

`tests/test_parity_minirag.py` fails if the file drifts from that pin.

## The recorded scoreboard

| Arm | Tasks | Tier 1 | Tier 2 | Tool calls | Prompt tokens | Completion tokens | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `bare` | 4/8 | 0/4 | 4/4 | 8 | 726 | 118 | 0.0081797 |
| `map` | 4/8 | 4/4 | 0/4 | 0 | 1212 | 204 | 0.0003042 |
| `map_rules` | 8/8 | 4/4 | 4/4 | 0 | 1212 | 204 | 0.0003042 |

Prices are the committed per-model table, not one global triple:
`{"input_usd_per_million": 0.15, "output_usd_per_million": 0.6, "tool_usd": 0.001}`
for `fixture-model`. The scoreboard's token totals include the four tier-2
judge calls (40 prompt and 10 completion tokens each). The harness scores the
same decisions with a deterministic metric and no judge, so its answer tokens
are exactly the recorded totals minus that judge block; the parity test asserts
both halves of that split, and cost within one cent of the committed figure.

## The recorded pipeline

`examples/parity/minirag_replay_server.py` is the recorded pipeline, not a mock
of the harness. It answers `POST /run` from these files, keyed by the request
prompt and the injected context:

- No context: it "retrieves" through `retrieval.json`. The tier-2 prompts have
  an entry; the tier-1 prompts do not, so the recorded pipeline refused before
  calling the model and reported zero usage and one tool call.
- A `map-*` chunk in the context: the recorded tier-1 answers became reachable.
- The task's rule chunk in the context: the recorded tier-2 answer and citation
  became reachable.

The replay server never reads a spec and never scores anything; it returns the
recorded answer, citations, usage and tool calls, exactly as the candidate did.