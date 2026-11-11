# Prompt: ledger update (stage 04b)

Inputs:
- {{ledger}}: the active rows of ledger.json (idea, type, question, tier needed)
- {{dossier}}: 04_dossier.md, every row's entries merged and numbered D01, D02, ...
- {{source_tiers}}: prompts/source_tiers.md
- {{followup_searches}}, {{followup_fetches}}: the follow-up agent's budget, research.budgets.followup

Output: JSON (structured output; SCHEMA in stages/ledger_update.py). Code
writes it into ledger.json and checks every "filled": it must have an entry at
the tier needed and a material entry, or the row becomes partial. Each open or
partial row then gets one follow-up agent, given its gap word for word, so
write the gap as an instruction that agent can act on.
---
You are the research editor for a long-form audio chapter. Before any writing, the chapter's ideas were set out in a ledger, and one research agent worked on each. Decide, row by row, whether the research did its job. A row the research has not settled will either get one more agent or be flagged to the commissioning editor, so judge strictly: a weak row now is a weak segment later.

The ledger's rows:

<ledger>
{{ledger}}
</ledger>

The research dossier. Each entry is tagged with the row or rows it came from, its kind (claim, material, quote, tension, connection) and its source tier; quotes say whether they are verbatim. The source check at the end lists problems the code found:

<dossier>
{{dossier}}
</dossier>

Source tiers:

{{source_tiers}}

For every row in the ledger, give:

- **status**:
  - "filled": the question is answered at the tier needed or better, and there is tellable material, a concrete scene, story, example or image that makes the idea land. For a covered row: its episode claims are checked against sources of the tier it needs or better (confirmed or corrected), and the episode's own material counts as tellable.
  - "partial": some of it is there, but the answer rests on sources below the tier needed, or leaves part of the question open, or there is no tellable material.
  - "open": the question is not answered.
- **dossier_refs**: every entry that serves the row, its own and any from other rows, as "D04".
- **best_tier**: the best tier among the entries that actually answer the question, or null if none do.
- **gap**: for a partial or open row, exactly what is missing, written as an instruction a follow-up agent can act on with {{followup_searches}} searches and {{followup_fetches}} page reads: what to find, and where to look for it (the primary text and passage, the encyclopedia entry, the kind of source). The follow-up agent sees the row's first findings under their own numbers, not the dossier's, so name the claim or source you mean in words, never by D number. Empty for a filled row.

And **connections**: the links between rows that the agents recorded as connection entries, merged and deduplicated. For each, the rows it joins and one sentence on the link, phrased so a writer could use it as the transition from one idea to the next. Keep only the links the agents found; do not invent new ones.

An answer that rests on an entry the source check flags, a quote downgraded to verbatim: no or a URL no search returned, does not count at that entry's tier.
