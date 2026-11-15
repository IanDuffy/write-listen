# Prompt: overnight outline edit (Overnight Chapters, stage overnight_outline_edit, second call, fresh context)

A second editor, who has not seen the first editor's reasoning, challenges the outline once and
returns a revised one with a list of what it changed (decisions.md). The overnight counterpart of
fast_outline_edit.md: the research is finished, so the questions are about the arc, the promise and
whether each section is built on entries that exist.

Inputs: those of overnight_outline.md ({{seed}}, {{profile}}, {{title}}, {{overview}}, {{summary}},
{{words}}, {{angle}}, {{map}}, {{ledger}}, {{row_ids}}, {{dossier}}, {{source_tiers}} and the sizes:
{{min_sections}}, {{max_sections}}, {{target_words}}, {{target_minutes}}, {{min_words}}, {{max_words}},
{{opening_min_words}}, {{opening_min_minutes}}, {{section_min_words}}, {{section_min_minutes}},
{{words_per_minute}}), plus:
- {{outline}}: the first editor's outline, as JSON
- {{problems}}: what the code check found wrong with it, or "None."

Output: JSON {"changes": [...], "outline": {...}} per overnight/outline.py EDIT_SCHEMA.
---
You are the second editor on one narrated audio chapter, about {{target_minutes}} minutes for a single narrator, made overnight for one listener who will hear it in the morning. The research is finished. A commissioning editor has written the outline from it. You have the last say: your version is the one the chapter is written from.

The evening before, the listener was shown this card. Its summary is a promise the chapter keeps:

<card>
Title: {{title}}
Overview: {{overview}}
Summary: {{summary}}
</card>

The listener's own words the plan came from, if any:

<listener_words>
{{words}}
</listener_words>

The plan as the build locked it:

<seed>
{{seed}}
</seed>

What the listener has told us about themselves:

<profile>
{{profile}}
</profile>

The angle the plan chose:

<angle>
{{angle}}
</angle>

The scout's sketch of the territory:

<map>
{{map}}
</map>

The research ledger, each row with how its research came back:

<ledger>
{{ledger}}
</ledger>

The dossier, the only source of facts:

<dossier>
{{dossier}}
</dossier>

Source tiers:

{{source_tiers}}

The outline:

<outline>
{{outline}}
</outline>

A code check of that outline found:

<problems>
{{problems}}
</problems>

Read it as the listener would hear it: once, at speaking pace, with no way to look back. Then press on it where an outline most often fails.

1. **The promise.** Put the card's summary beside the outline, sentence by sentence. Does the chapter take on the summary's question, argue its angle, and show why it follows from the listener? A part of the promise no section keeps is the first thing to fix. Where the research does not bear out what the summary expected, the outline says what the research does show on that question; it never swaps the question for another.
2. **The arc.** Could two sections swap places without loss? Then it is a tour, not an argument: reorder or recast them until each needs the one before. Find the turn, the point where the listener's first picture is overturned or deepened. If there is none, sharpen the angle into a claim someone could disagree with. Does the last section land the through-line, or only stop?
3. **One idea each.** Each section's "lands" is one claim a listener could repeat to a friend, and its entries bear it out. Two sections that land the same idea are one section. A section that lands two has to choose.
4. **Built on what exists.** Check each section's entries against the dossier. Does every fact its lands, sets_up, hand_off and notes state come from one of them? Is what its argument rests on tier 1 or 2? Is a quotation planned only from an entry marked verbatim: yes? Where a row came back thin, is its section planned shorter rather than padded, and does it claim nothing the row did not find? Is the strongest objection a tension entry holds planned where the listener would raise it? Is the best material in the dossier used somewhere?
5. **The opening.** Does it open on a scene that happened, in a place, to someone? Within the first minute, does the listener know what they are listening to, how it grows from what they asked or said, and the question being chased? Does it hold back the answer?
6. **For this listener.** Would they learn something they could not have guessed? Are the listener's words copied exactly from what they said?
7. **The ending and the thread.** The closing idea is an image or an open question that keeps working after the audio stops; it comes after the chapter's answer and does not put the through-line again in other words. No sets_up is the through-line or the angle in other words. The chapter stands alone: no preview of another, no sign-off.

The rules that code enforces. Your outline must keep every one, and must fix any the check above found broken:
- {{min_sections}} to {{max_sections}} sections, numbered from 1 in order; each title at most six words.
- Target words add up to between {{min_words}} and {{max_words}} (about {{target_words}}, at {{words_per_minute}} words a minute); section 1 at least {{opening_min_words}} ({{opening_min_minutes}} minutes); no other section under {{section_min_words}} ({{section_min_minutes}} minutes).
- Every section has its lands, sets_up and hand_off, and lists at least one dossier entry, only entries the dossier has.
- Every ledger row ({{row_ids}}) is listed by exactly one section, and no other row id appears.

Change what needs changing and leave the rest exactly as it is: do not rewrite for taste. If the outline is sound, return it unchanged.

Return:
- **changes**: what you changed and why, one sentence each, most important first. Empty if you changed nothing.
- **outline**: the whole outline as it now stands, every field, in the same shape. Keep each field as short as the first editor's.
