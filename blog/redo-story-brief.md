# Redo story brief (0.4)

You are writing part of a corporate engineering blog for the eval-harness project at D:\projects\eval-harness. Work only in that repo. Do not edit anything outside blog/.

## Role and voice
Write as a forward deployed engineer who built this with a client. Do not name the role, the client, or any person. First person plural. Plain, concrete, low-jargon. Short paragraphs. No em dashes. American spelling. No invented results, quotes, or client names. Read blog/eval-harness-blog.md first for voice.

## Task
Rewrite only these three sections for a new version of the blog:
1. "The tie"
2. "Why the scoreboard broke"
3. "Start with the receipt"

Keep the existing story spine and the exact numbers. The new version must add the 0.4 hook: the code under test became a variable. Cases and fixtures live next to the code they test; copying them into a spec creates a second copy that drifts; a run could not name the tree it read. 0.4 makes the repository a named, content-addressed input and makes context selection per case behind a sixth protocol. Do not turn this into a release-notes list. One or two concrete sentences in "Why the scoreboard broke" and one in "Start with the receipt" is enough.

## Facts you may use (verified; do not add others)
- Three arms: bare 4/8 (tier 1: 0/4, tier 2: 4/4, cost $0.0081797); map 4/8 (4/4, 0/4, $0.0003042); map_rules 8/8 (4/4, 4/4, same cost as map). Ratio 26.9x, write "roughly 27 times". Bare's eight retrieval tool calls at $0.001 each are most of its cost.
- Tier 1: four questions about which symbol a Java loan class calls. Tier 2: four policy questions about what rule a loan states in a named bank.
- The winner to ship was map_rules, eight of eight at map's cost.
- 0.4.0 is "The Context release" (2026-11-03): repository snapshots, a repository source, per-case context selectors, a sixth protocol, and evalh repos gc.
- A repo is declared with a local root or an HTTPS URL, optional ref and credential. Before the first cell the harness copies included files into a content-addressed tree under $EVALH_HOME/repos/<digest>, records digest, commit, dirty flag and file counts on the run, and refuses a resume whose tree changed. The repository source reads cases and fixtures from that snapshot.
- A context entry is a fixed chunk list or a selector with an adapter and options. The planner calls it per candidate and case; the cell records chunk ids, selector name and digest.
- Exact claims only. If unsure, leave it out.

## Structure and length
- "The tie": about 430-480 words. Open in the room, the narrow question, the three arms, the table, the opposite tier failures, the cost, the realization that the question was too small.
- "Why the scoreboard broke": about 300-340 words. The questions multiplied, each script had no memory, no spec hash, no price snapshot, no judge pin, no interval. Then the repo-drift paragraph: cases next to code, a copied case file drifts, a green run cannot name the tree, two green runs can differ because the repo moved. End with the need for a different object, a harness, not a better script.
- "Start with the receipt": about 140-180 words. Start with twenty cases, one candidate, one gate, CI, keep the receipt. Add: name the tree, snapshot the repo before the first case, because the code will move. End with "A demo is a claim. The harness is the evidence." (Use that exact closing line.)

## Output
Write the three sections, with their headings, to blog/redo-story.md. Do not write the rest of the blog. Then reply with a 3-sentence summary and the word count.