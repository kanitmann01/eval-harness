# Your Demo Is Not Evidence

*A demo proves a thing can happen once. A release needs to know how often, at what cost, and whether it will happen again. This is the story of a table that answered the first question and not the others.*

## The question after the demo

The demo went well, which told us almost nothing. Three context configurations were on the table, eight tasks each, and the room wanted a decision.

The tasks split cleanly: four asked which symbol a loan file calls, four asked what rule a loan states in a given bank. `bare` injected no context and scored 4/8, with nothing on the code questions and everything on the policy questions, at $0.0081797. `map` injected a mapped excerpt and scored 4/8 the other way, all four code questions and none of the policy ones, at $0.0003042. `map_rules` added the rule text and scored 8/8 at the same $0.0003042.

Two arms, same headline, opposite failures. The costs were 27 times apart. Four failed retrieval calls in `bare` were about half its bill, which is its own small lesson about where the money goes when retrieval misses.

We brought the table back to the room.

"Which one should we ship?"

"The table says these two are the same."

"Then the table is wrong."

It was not wrong about the count. It was wrong as an answer. The table could not say why the two arms failed in opposite directions, because it did not record which repository tree the retriever ran against, which judge version scored the policy answers, which prices it had used, or how much of that 4/8 was noise. A score without provenance is an opinion with a decimal point.

The narrow answer was `map_rules`. The useful finding was that we could not say why it would keep working.

## Why now

The parts move independently. The model changes, the prompt changes, the context strategy changes, the judge changes, the repository changes, and none of them waits for the others. A demo tests one arrangement on one afternoon. A release has to test several arrangements, repeatedly, and compare them honestly.

An ad hoc script encodes one arrangement in one file. Change the model and the script needs an edit. Change the judge and the score moves without a warning. Change the repository and the cases may no longer mean what they meant. By the next release nobody can say which version produced the green number, so the green number becomes folklore.

Cost belongs in the same sentence. A candidate that wins by one task at many times the price is not a win; it is a different decision. A cost column shows what you spent. It does not show what the spending bought. A judge swap is worse, because it looks like progress. The number goes up, the reason is a new judge, and the meeting ends early. Judges cost money too. A model judge is one more thing that can change without the prompt changing.

That is the standard a release question deserves: the same cases, the same repository state, the same judge configuration, the same prices, and enough samples that a one-task difference is not read as a signal. None of it is exotic. All of it was missing from the script that printed our table.

Here is the uncomfortable version of the argument. Most teams do not have an evaluation problem. They have a demo problem wearing evaluation's clothes. The demo worked, the deck has a chart, and the chart has no receipt. A number you cannot reproduce is not a number. It is a memory of a number.

The script answered the question once. The release asks it every week.

## Twenty cases and a receipt

The fix is smaller than the argument. Start with twenty cases you can defend, one candidate, and one gate that fails the build when the number drops. Keep the receipt: the spec, the repository state, the judge configuration, the prices. Keep the interval next to the score. Add a second candidate when the first number gets boring, which it will. The first run will be embarrassing. That is fine, as long as the second run can be compared to it.

None of this makes a model trustworthy. It makes a result auditable, which is what a release meeting can actually use. A demo is a claim. The harness is the evidence.
