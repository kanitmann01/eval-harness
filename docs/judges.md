# Judges and judge-backed metrics

A judge turns an answer into a verdict. It is a metric's dependency, not a
global setting: two metrics in one run may name two different judges at two
different costs, and each check records the configuration that produced it.
The contract lives in [core/protocols.py](../src/evalharness/core/protocols.py)
and the wiring in [core/judges.py](../src/evalharness/core/judges.py).

## The contract

~~~python
class Judge(Protocol):
    name: str
    async def verdict(self, request: JudgeRequest) -> Verdict: ...
~~~

`verdict` is async. A judge receives a `JudgeRequest` with the case id, the
prompt, the text to judge in `answer`, the optional rubric and expected data,
and an `evidence` mapping. It returns a `Verdict`:

| Field | Meaning |
| --- | --- |
| `passed` | the verdict, a bool |
| `score` | optional numeric score for a future calibration view |
| `reason` | non-empty explanation, required by `judge/3` |
| `evidence` | JSON-serialisable mapping, required by `judge/4` |
| `prompt_tokens`, `completion_tokens` | what the judge spent producing this verdict |
| `model` | the model the adapter reports |
| `usage_source` | `provider` or `estimated` |

A judge is deterministic in the suite's sense: the same request produces the
same verdict, which is `judge/5`. The conformance suite is the contract; see
[adapters.md](adapters.md).

## Reference judges

Both are first-party, credential-free and network-free.

| Adapter | What it does |
| --- | --- |
| `judges.rule_based:build` | passes when the expected answer is a substring of the answer; no model, no tokens, no cost |
| `judges.scripted:build` | deterministic rubric/claim judge with estimated token usage, so a run can price it |

`scripted` options:

| Option | Default | Meaning |
| --- | --- | --- |
| `claims` | unset | list of claims every one of which must appear in the judged text |
| `instructions` | `""` | judge instructions; counted in the prompt usage and kept in the evidence |
| `model` | `scripted-judge` | the model the verdict reports |
| `prompt_tokens` | word count of the request | explicit prompt-token override |
| `completion_tokens` | claims + 1 | explicit completion-token override |

Claims come from, in order: `options.claims`, the case `rubric` (one claim
per non-empty line), `expected.claims`, then `expected.answer`. With no
claims the verdict is a fail and the reason says why. Usage is estimated
unless options pin it, and the verdict records `usage_source: estimated`.

## Wiring a judge to a metric

A spec declares judges by name, then a metric names one under `options.judge`:

~~~yaml
metrics:
  - key: citation_support_strict
    adapter: evalharness.adapters.metrics.citation_support:build
    options: {judge: strict-judge}
  - key: citation_support_cheap
    adapter: evalharness.adapters.metrics.citation_support:build
    options: {judge: cheap-judge}

judges:
  - name: strict-judge
    adapter: evalharness.adapters.judges.scripted:build
    model: scripted-strong
    options: {model: scripted-strong, instructions: Require every claim.}
  - name: cheap-judge
    adapter: evalharness.adapters.judges.scripted:build
    model: scripted-cheap
    options: {model: scripted-cheap, instructions: Accept a citation that contains the claim.}

prices:
  scripted-strong: {input: 2.00, output: 8.00}
  scripted-cheap: {input: 0.10, output: 0.40}
~~~

Validation rejects a metric whose `options.judge` is not a declared judge,
before anything runs. It also rejects a declared judge `model` with no price.
The complete two-judge spec is
[examples/specs/judges.yaml](../examples/specs/judges.yaml).

At scoring time the executor builds a fresh `JudgeSession` per declared judge
for each cell. The session wraps the shared adapter and records the tokens and
cost this cell accumulated, so a concurrent run attributes every verdict to
the cell that triggered it. `ScoreContext.judges` is that mapping; a metric
looks its judge up by the name in its options and awaits it. The context also
carries the run's redactor, which a metric applies to any text it hands to a
judge or writes into evidence.

## The configuration hash

Every check a judge produced carries `judge_config_hash`, a sha256. Checks
from deterministic metrics carry `None`. The hash covers the declaration,
not the judge's name:

~~~text
sha256(json.dumps({
    "adapter": "<adapter path>",
    "model": "<declared model or empty string>",
    "options": <declared options mapping>,
}, sort_keys=True, separators=(",", ":")))
~~~

`sort_keys` sorts nested mappings too, so declaration order is irrelevant.
Temperature and instructions live in `options` and therefore change the hash.
Changing the model changes it; changing an option changes it; a second run of
the same declaration produces the same hash. Name and credential are excluded
because they select a declaration rather than change its behaviour. Judge
calibration (agreement against human labels) is keyed by this hash in a later
ticket, so drift is visible rather than trusted.

## Cost attribution

Judge usage is priced from the spec price table by the judge's declared
`model`, never by the candidate's model and never by the verdict's reported
model. A judge without a declared model has no price and costs zero while
still recording the tokens it reported.

The executor splits every cell's cost and stores it on the cell:

| Column | Meaning |
| --- | --- |
| `answer_cost_usd` | candidate prompt and completion tokens at the candidate price |
| `judge_cost_usd` | every verdict this cell triggered, at each judge's price |
| `tool_cost_usd` | candidate tool calls at the candidate price |
| `cost_usd` | the sum of the three, always |
| `judge_prompt_tokens`, `judge_completion_tokens` | judge tokens this cell spent |

The run line prints the split:

~~~text
run <id> status=ok cells=1 ok=1 cost=$0.000691
cost: answer $0.000007, judge $0.000685, tools $0.000000
~~~

A deterministic-only run prints `judge $0.000000` and stores zeros, with
`None` hashes on every check.

## A judge-backed metric

`metrics.citation_support:build` takes the cell's answer and citations, asks
the named judge whether each citation supports the answer, and returns one
check. It fails when there are no citations or any citation is unsupported,
and its evidence lists every citation with the judge's pass/fail and reason:

~~~json
{
  "judge": "strict-judge",
  "answer": "The answer is 42.",
  "citations": [
    {"citation": "6 times 7 is 42.", "passed": true, "reason": "all 1 claim(s) are supported by the answer"}
  ]
}
~~~

The judged text is the citation, because support is a property of each
citation; the candidate's answer travels in `JudgeRequest.evidence`. The
check records the judge's `config_hash`, so two metrics in one run produce
two different hashes and two different verdict reasons.
