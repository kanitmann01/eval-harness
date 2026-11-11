"""The demo library: metadata, bundled specs and first-use materialisation.

A demo is a normal spec file copied into the operator's specs directory the
first time it is run or opened. After that the file belongs to the operator:
edits are kept and deleting the file restores the bundled copy on the next use.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from evalharness.api.spec_store import SpecStore
from evalharness.core.config import Settings

PIPELINE: tuple[str, ...] = (
    "Spec",
    "Cases",
    "Cells",
    "Checks",
    "Aggregates",
    "Gates",
    "Verdict",
)
"""The seven stages a demo walks an operator through, in order."""


@dataclass(frozen=True, slots=True)
class DemoScenario:
    """One scripted walkthrough and the question it answers."""

    slug: str
    title: str
    question: str
    varies: str
    look_for: str
    expected: str
    spec_file: str
    cost_note: str = "no spend"
    duration_note: str = "under a second"

    @property
    def spec_name(self) -> str:
        """Return the spec file stem used in the operator's specs directory."""
        return f"demo-{self.slug}"

    @property
    def edit_url(self) -> str:
        """Return the console editor path for the materialised spec."""
        return f"/specs/{self.spec_name}/edit"


DEMOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        slug="prompt-choice",
        title="Prompt choice",
        question="Does a stricter prompt produce a better answer?",
        varies="Two prompt variants: one asks for digits only, one asks for a "
        "short clause. Same stub model, same case.",
        look_for="The strict candidate passes exact_match. The explained "
        "candidate passes contains but fails exact_match, so the pooled gate "
        "fails even though both answers are true.",
        expected="The gate, not the model, decides which answer is better.",
        spec_file="prompt-choice.yaml",
    ),
    DemoScenario(
        slug="context-grounding",
        title="Context grounding",
        question="What changes when the policy text is in context?",
        varies="One candidate receives the refund-policy chunk; the other "
        "receives no context. Same prompt and model.",
        look_for="The grounded answer is 30 days and passes exact_match. The "
        "ungrounded answer admits it does not have the policy and fails the "
        "gate.",
        expected="Context is visible in the trace and the gate catches an "
        "ungrounded answer.",
        spec_file="context-grounding.yaml",
    ),
    DemoScenario(
        slug="model-tradeoff",
        title="Model and cost tradeoff",
        question="If both models are right, which one should ship?",
        varies="A cheap candidate and a strong candidate both answer 42, with "
        "different token counts and prices.",
        look_for="Both checks pass and the gate clears. The trace still shows "
        "the cost and token spread, so correctness alone does not pick a "
        "winner.",
        expected="A passing gate is not the same as a good tradeoff.",
        spec_file="model-tradeoff.yaml",
    ),
    DemoScenario(
        slug="failure-path",
        title="A failed candidate",
        question="What happens when one candidate cannot answer?",
        varies="Two cases, three candidates: a fast stub, a slow stub and one "
        "whose transport always fails.",
        look_for="The failing cell is recorded as an error with its message. "
        "It contributes no check, so the remaining checks still pass the gate "
        "and the run is partial rather than silently clean.",
        expected="A failed cell is evidence, not a missing row.",
        spec_file="failure-path.yaml",
    ),
)
"""The demo library in display order."""


def find_demo(slug: str) -> DemoScenario | None:
    """Return the scenario with this slug, or None when it is unknown."""
    return next((demo for demo in DEMOS if demo.slug == slug), None)


def bundled_text(scenario: DemoScenario) -> str:
    """Return the bundled YAML for one scenario as UTF-8 text."""
    return (
        resources.files("evalharness.demos")
        .joinpath(scenario.spec_file)
        .read_text(encoding="utf-8")
    )


def materialize(settings: Settings, scenario: DemoScenario) -> Path:
    """Copy a bundled demo into the specs directory only when absent.

    The user's copy wins: an edited demo is never overwritten and a deleted
    demo is restored from the package on the next run.
    """
    store = SpecStore(settings.specs_dir)
    if store.exists(scenario.spec_name):
        return store.path_for(scenario.spec_name)
    return store.write(scenario.spec_name, bundled_text(scenario))


def materialized_slugs(settings: Settings) -> tuple[str, ...]:
    """Return the slugs whose demo spec already exists in the specs directory."""
    store = SpecStore(settings.specs_dir)
    return tuple(demo.slug for demo in DEMOS if store.exists(demo.spec_name))


def scenario_rows(settings: Settings) -> list[dict[str, object]]:
    """Return the page-ready rows for every demo, with its copied state."""
    copied = set(materialized_slugs(settings))
    return [
        {
            "slug": demo.slug,
            "title": demo.title,
            "question": demo.question,
            "varies": demo.varies,
            "look_for": demo.look_for,
            "expected": demo.expected,
            "spec_name": demo.spec_name,
            "edit_url": demo.edit_url,
            "cost_note": demo.cost_note,
            "duration_note": demo.duration_note,
            "materialized": demo.slug in copied,
        }
        for demo in DEMOS
    ]
