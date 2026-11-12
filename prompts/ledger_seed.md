# Prompt: ledger, seed run (stage 03b)

The seed-run variant of ledger.md (same JSON). The lesson starts from a
listener's seed and the scout's map; the row types are judged against what the
scout found, and the JSON field episode_has holds what the scout's map already
has. Keep the two files in step.

Inputs:
- {{angle}}: 03_angle.md, the angle the choose stage picked
- {{seed}}: 00_source/seed.md
- {{map}}: 01_map.md, the scout's map and sources
- {{min_rows}}, {{max_rows}}, {{target_minutes}}: config.yaml (seed_runs)
- {{budgets}}, {{source_tiers}}
---
We are making one narrated audio lesson, about {{target_minutes}} minutes for a single narrator, for one listener, from a topic they asked about, along one angle the commissioning editor has chosen. The listener is curious and not a specialist.

Before any research, we decide what the lesson must land. That is the ledger: a short list of ideas, ranked. Each idea gets its own research agent, aimed by the angle rather than by general reading. After the script is drafted, the same ledger is used to check which ideas the script actually landed.

Depth beats breadth. A {{target_minutes}}-minute lesson lands {{min_rows}} or {{max_rows}} ideas well, not six thinly.

The chosen angle, with the editor's notes:

<angle>
{{angle}}
</angle>

What the listener said:

<seed>
{{seed}}
</seed>

A scout's map of the territory: its threads, people, sources, stories and claims:

<map>
{{map}}
</map>

Write the ledger.

**rows**: the {{min_rows}} to {{max_rows}} ideas the angle needs, ranked by importance to the angle (rank 1 is the idea the lesson can least do without). For each row:

- **idea**: one sentence stating what the lesson must make the listener understand. An idea is a claim or a move in the argument, something a listener could repeat to a friend. "Plato's ladder of love ends in a beauty that no longer needs any one beloved" is an idea; "Plato's Symposium" is a topic.
- **rank**: 1, 2, 3, ... with no ties.
- **type**: one of
  - "deep-dive": the scout found it, but only on the surface, so research must go deeper;
  - "missing": the angle needs it and the scout found nothing on it;
  - "covered": the scout's sources already settle it, so research only checks the facts the lesson would take from them.
- **episode_has**: what the scout's map already has for this idea, with its source URLs. For a missing row, what little there is, or "Nothing."
- **research_question**: for deep-dive and missing rows, the one specific question research must answer. Make it narrow enough to settle within one agent's budget ({{budgets}}): name the primary text and passage, the person, the event or the kind of source that would settle it. For covered rows, instead list the specific factual claims from the scout's sources that the lesson would repeat, numbered, each with its source. Popular sources repeat errors in dates, names, attributions and quotations, and each of these claims will be checked so the lesson does not repeat one.
- **tier_needed**: the minimum source tier that would settle the question, from this table:

{{source_tiers}}

Anything the lesson's argument rests on needs tier 1 or 2. Covered rows are checked against tier 1 or 2 sources.

**parked**: good ideas that did not make the cut, one line each. They are not researched; they are seeds for a later lesson.

Make the rows add up to the angle: taken in rank order, they should cover what the thesis needs and nothing it doesn't. Two rows that would send research to the same pages are one row.
