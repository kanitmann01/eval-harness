## Title options

1. **Two Arms, One Score, Opposite Failures** (pick)
2. The Demo Was a Claim
3. What a Green Scoreboard Hides

*The policy assistant worked in the room. Then we asked whether prompt v4 beat v3 and got 4/8 against 4/8: two arms tied while failing opposite halves, at roughly 27 times the cost. The scoreboard was accurate and useless.*

## The tie

The demo ran in a conference room with a laptop and a projector. A policy assistant answered questions over a loan-policy corpus, and it worked in the room: the citations landed on the right sections and the answers stayed short.

The first question after the demo was not about the assistant. It was about a prompt. We had a v3 and a v4, and the bank wanted to know which one to ship. That is a narrow question, narrow enough to answer with a script: run both prompts, plus the pipeline as it stood, score the same eight tasks, print a table.

Three arms. `bare` was the pipeline with no injected context. `map` added a mapped excerpt of the corpus. `map_rules` added the rule text on top of the map. Eight tasks in two tiers: four code questions ("which symbol does Loan1 call?") and four policy questions ("what rule does Loan1 state in bank-1?").

The table came back like this. `bare` 4/8, `map` 4/8, `map_rules` 8/8.

Two arms tied on the headline number, and the headline was the least interesting thing in the table. Bare passed zero of four tier-1 tasks and all four tier-2 tasks. Map passed all four tier-1 and zero tier-2. Same score, opposite failures, for opposite reasons. Bare refused the code questions before it ever called the model, because its retriever had no entry for them. Map carried the structure that answered those questions and nothing that answered the rules.

Then the cost column. Bare cost $0.0082 for the same eight tasks. Map cost $0.0003. Roughly 27 times apart, and most of bare's bill was eight retrieval tool calls at a tenth of a cent each, not tokens. The expensive arm did not buy a single extra pass.

A single number said the two arms were equivalent. The table said one failed at retrieval and the other failed at context, and neither failure was visible in the headline. We had answered the question we were asked. We had also learned that the question was too small.

## Why the scoreboard broke

The script answered one question once. Then the questions changed shape. Can a cheaper model hold the line? What happens with a new judge? Does more context help or hurt? A teammate's agent is ready, so how does it compare? Why did last week's green run differ from this week's?

Each one meant a new script, and each script produced an answer with no memory of the last one. A score arrived as a number in a terminal, and the number had no provenance. No spec hash, so we could not say which version of the suite produced the number. No price snapshot, so a cost comparison might be priced at rates that had already moved. No judge pin, so a judge model or prompt change could move the score without leaving a mark. No interval, so 4/8 against 5/8 looked like a difference when it was noise. Two green runs could differ, and nobody could say why.

That is survivable when evaluation is a quarterly study. It is not survivable when model and prompt changes ship weekly, because "is it better?" stops being a study and becomes a release question, asked every week, against last week's number. Judges make it worse. They are useful and unstable, and a silent judge swap reads exactly like a quality improvement. Cost makes it worse again. A candidate that scores one point higher at thirty times the cost is not a better candidate; it is a different decision, and the scoreboard had no place to put that.

The failure was not the script. The script was correct. The failure was that every number lived alone, with nothing attached that could tell us whether two numbers were comparable.

What we needed was not a better script but a different object: a harness.
