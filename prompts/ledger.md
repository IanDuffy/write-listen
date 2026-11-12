# Prompt: ledger (stage 03b)

Inputs:
- {{angle}}: 03_angle.md, Ian's chosen angle and notes
- {{transcript}}: 00_source/transcript.txt, in full (no summarizing step)
- {{map}}: 01_map.md
- {{min_rows}}, {{max_rows}}: config.yaml, research.min_rows and max_rows
- {{target_minutes}}: config.yaml, chapter.target_minutes
- {{budgets}}: each research agent's search budget, from config.yaml
- {{source_tiers}}: prompts/source_tiers.md

Output: JSON (structured output; the schema is SCHEMA in stages/ledger.py).
Code numbers the rows L01, L02, ... in rank order and writes ledger.json and
ledger.md. Ian then reviews the ledger and can add, cut, re-rank, park or
rewrite rows before research runs.
---
We are commissioning one long-form audio chapter, about {{target_minutes}} minutes for a single narrator. It starts from a podcast episode and goes further than the episode did, along one angle the commissioning editor has chosen. The listener is curious and not a specialist.

Before any research, we decide what the chapter must land. That is the ledger: a short list of ideas, ranked. Each idea gets its own research agent, aimed by the angle rather than by general reading. After the script is drafted, the same ledger is used to check which ideas the script actually landed.

Depth beats breadth. A {{target_minutes}}-minute chapter lands {{min_rows}} to {{max_rows}} ideas well, not twice as many thinly.

The chosen angle, with the editor's notes:

<angle>
{{angle}}
</angle>

The episode transcript:

<transcript>
{{transcript}}
</transcript>

A map of the episode: its thinkers, works, claims, examples, and the threads it skims:

<map>
{{map}}
</map>

Write the ledger.

**rows**: the {{min_rows}} to {{max_rows}} ideas the angle needs, ranked by importance to the angle (rank 1 is the idea the chapter can least do without). For each row:

- **idea**: one sentence stating what the chapter must make the listener understand. An idea is a claim or a move in the argument, something a listener could repeat to a friend. "Plato's ladder of love ends in a beauty that no longer needs any one beloved" is an idea; "Plato's Symposium" is a topic.
- **rank**: 1, 2, 3, ... with no ties.
- **type**: one of
  - "deep-dive": the episode raises it but skims it, so research must go deeper;
  - "missing": the angle needs it and the episode does not cover it;
  - "covered": the episode already gives enough, so research only checks its facts.
- **episode_has**: what the transcript already says about this idea, with a short pointer to where: who says it, and a few of their words so the passage can be found. For a missing row, what little there is, or "Nothing."
- **research_question**: for deep-dive and missing rows, the one specific question research must answer. Make it narrow enough to settle within one agent's budget ({{budgets}}): name the primary text and passage, the person, the event or the kind of source that would settle it. For covered rows, instead list the specific factual claims from the episode that the chapter would repeat, numbered, each closely paraphrased with who made it. Speakers in a live conversation misremember dates, names, attributions and the order of events, and each of these claims will be checked so the chapter does not repeat an error.
- **tier_needed**: the minimum source tier that would settle the question, from this table:

{{source_tiers}}

Anything the chapter's argument rests on needs tier 1 or 2. Covered rows are checked against tier 1 or 2 sources.

**parked**: good ideas that did not make the cut, one line each. They are not researched; they are seeds for a later chapter.

Make the rows add up to the angle: taken in rank order, they should cover what the thesis needs and nothing it doesn't. Two rows that would send research to the same pages are one row.
