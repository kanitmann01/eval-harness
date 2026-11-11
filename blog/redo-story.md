## The tie

The demo worked in the room. A policy assistant answered questions over a loan-policy corpus: the citations landed on the right sections, the answers stayed short, and the conversation moved on to what came next.

The first question after the demo was not about the assistant. It was about the pipeline. We had three ways to feed it context, and the client wanted to know which one to ship. That is a narrow question, narrow enough to answer with a script: run all three arms over the same eight tasks and print a table.

`bare` was the pipeline with no injected context. `map` added a mapped excerpt of the corpus. `map_rules` added the rule text on top of the map. Eight tasks in two tiers: four questions about which symbol a Java loan class calls, and four policy questions about what rule a loan states in a named bank. Each arm's number was really two numbers glued together. The split was deliberate. Half the tasks looked like code navigation and half looked like document lookup, and the three arms differed only in what context they carried into the prompt.

The table came back like this.

| Arm | Tier 1 | Tier 2 | Total | Cost (USD) |
| --- | --- | --- | --- | --- |
| bare | 0/4 | 4/4 | 4/8 | $0.0081797 |
| map | 4/4 | 0/4 | 4/8 | $0.0003042 |
| map_rules | 4/4 | 4/4 | 8/8 | $0.0003042 |

Two arms tied on the headline number, and the headline was the least interesting thing in the table. Bare passed zero of four tier-1 tasks and all four tier-2 tasks. Map passed all four tier-1 and zero tier-2. Same score, opposite failures, for opposite reasons: bare refused the code questions before it ever called the model, because its retriever had no entry for them, while map carried the structure that answered those questions and nothing that answered the rules. Neither arm was weak across the board. Each was strong exactly where the other was empty.

Then the cost column. Bare cost $0.0081797 for the same eight tasks. Map cost $0.0003042. Roughly 27 times apart. Most of bare's bill was eight retrieval tool calls at $0.001 each, so the arm that failed the code questions spent nearly all its money trying to find them, and its tokens were a rounding error beside that.

A single number said the two arms were equivalent. The table said one failed at retrieval and the other failed at context, and neither failure was visible in the headline. We had answered the question we were asked: ship `map_rules`, eight of eight at map's cost. We had also learned that the question was too small. The score we were optimizing could not tell a retrieval failure from a context failure, and those ask for different fixes.

The demo is the easy part. The Monday after is the work.

## Why the scoreboard broke

The script answered one question once. Then the questions changed shape. Can a cheaper model hold the line? Does more context help or hurt? How does a teammate's agent compare? Why did last week's green run differ from this week's, when nobody changed the prompt?

Each one meant a new script with no memory of the last. A score arrived as a bare number in a terminal: no spec hash, so we could not say which cases ran; no price snapshot, so a cost comparison might use rates that had moved; no judge pin, so a silent judge swap could move the score; no interval, so a 4/8 against a 5/8 looked like a difference.

That works when evaluation is a quarterly study. It does not when changes ship weekly: "is it better?" stops being a study and becomes a release question, asked every week, by different people, against last week's number.

Judges and cost make a scoreboard lie: a silent swap looks like an improvement, and a point bought at many times the price looks like progress.

Then the code under test became a variable. Cases and fixtures live next to the code they test, so we copied them into the spec, and from that day there were two copies of every case file, drifting. A green run could not name the tree it read: no digest, no commit, no dirty flag. Two green runs could differ because the repository moved under them, and the receipts would look identical. So 0.4 made the repository a declared input, copied before the first cell into a content-addressed tree under a digest, and made context a per-case choice behind a sixth protocol.

The failure was not the script. Every number lived alone, with nothing attached that could say whether two numbers were comparable, or which tree produced them.

What we needed was not a better script but a different object: a harness. Not a research tool, but the release infrastructure for systems whose behavior you cannot read from the diff.

## Start with the receipt

If you are building this for the first time, do not start with a platform. Start with twenty cases that hurt, one candidate you already trust, and one gate you are willing to fail a build over. Twenty is enough to catch the failures that matter. Run it in CI. Keep the receipt. Add a second candidate only when the first one's number is boring.

Name the tree before you name a winner. The repository is an input, not a background assumption: snapshot it before the first case runs, hold the digest next to the score, and refuse a resume that would quietly read a different tree, because the code will move faster than the prompt. A receipt that cannot name the tree is an opinion. The model will change. The prompt will change. The judge will change. The receipt should still mean something.

A demo is a claim. The harness is the evidence.
