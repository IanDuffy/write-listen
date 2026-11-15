# Prompt: overnight outline repair (Overnight Chapters; only when the edited outline fails the code check)

One call, told exactly which rules the outline broke (overnight/outline.py validate). It mends those
and nothing else: the arc has had its edit. Word targets alone never reach it: code scales those first
(repair_targets). If its result still fails, code scales the word targets again; if that is not
enough, the first outline stands unedited when it is sound, and otherwise the build stops. Stage
settings: overnight_outline_edit's. The overnight counterpart of fast_outline_repair.md.

Inputs: those of overnight_outline.md (only {{ledger}}, {{row_ids}}, {{dossier}} and the sizes are used
here), plus:
- {{outline}}: the edited outline, as JSON
- {{problems}}: the rules it broke, one per line

Output: the whole outline, JSON per overnight/outline.py SCHEMA.
---
This is the outline of a narrated audio chapter made overnight for one listener, from finished research. Two editors have settled its angle and its arc. A code check then found that it breaks these rules:

<problems>
{{problems}}
</problems>

The outline:

<outline>
{{outline}}
</outline>

The research ledger, each row with how its research came back:

<ledger>
{{ledger}}
</ledger>

The dossier, whose numbered entries are the only ones a section may list:

<dossier>
{{dossier}}
</dossier>

The rules, all of which the outline must keep:
- {{min_sections}} to {{max_sections}} sections, numbered from 1 in order; each title at most six words.
- Target words add up to between {{min_words}} and {{max_words}} (about {{target_words}}, at {{words_per_minute}} words a minute); section 1 at least {{opening_min_words}} ({{opening_min_minutes}} minutes); no other section under {{section_min_words}} ({{section_min_minutes}} minutes).
- Every section has its lands, sets_up and hand_off, and lists at least one dossier entry, only entries the dossier has.
- Every ledger row ({{row_ids}}) is listed by exactly one section, and no other row id appears.

Mend what the problems name, in the smallest change that keeps the arc: a row listed twice stays with the section that does most with it; a row listed nowhere goes to the section whose idea it serves; an entry that does not exist is replaced by the one the section means, or dropped. Leave everything else exactly as it is. Return the whole outline, every field.
