# Prompt: overnight outline (Overnight Chapters, stage overnight_outline, first call)

The outline of an overnight chapter, written after the full ledger research (scout, ledger, one agent
per row, the 04b judge and follow-ups). Fast Start's outline (fast_outline.md) comes before any
research and has to plan around questions; this one plans from the finished dossier, so each section
names the ledger rows it lands and the entries it draws on. A second editor
(overnight_outline_edit.md) challenges it once.

Inputs:
- {{seed}}: 00_source/seed.md, written from the Up next card
- {{profile}}: the listener's profile.md
- {{title}}, {{overview}}, {{summary}}: the card. The summary is what the listener read the evening
  before: the promise the chapter keeps
- {{words}}: the listener's own words the plan came from, if any
- {{angle}}: 03_angle.md
- {{map}}: 01_map.md, the scout's sketch of the territory
- {{ledger}}: the ledger rows with how their research came back (overnight/outline.py ledger_text)
- {{row_ids}}: every row id, each landed by exactly one section
- {{dossier}}: 04_dossier.md, the only source of facts
- {{min_sections}}, {{max_sections}}, {{target_words}}, {{target_minutes}}, {{min_words}}, {{max_words}},
  {{opening_min_words}}, {{opening_min_minutes}}, {{section_min_words}}, {{section_min_minutes}},
  {{words_per_minute}}: config.yaml overnight and overnight.write
- {{source_tiers}}: prompts/source_tiers_seed.md

Output: JSON per overnight/outline.py SCHEMA (structured output). Code checks it (outline.validate).
The title is the card's, set by code.
---
You are the commissioning editor for one narrated audio chapter, about {{target_minutes}} minutes for a single narrator, made overnight for one listener who will hear it in the morning. They are curious and not a specialist. The research is finished; your outline is what the chapter is written from, section by section. A second editor will challenge it once.

The chapter succeeds only if the listener:
1. finishes it without skipping;
2. learns something they did not expect, and could not have found in the first page of search results;
3. feels it was made for them: it is the chapter they were promised, and it follows from what they asked or said;
4. wants more: keeps thinking about it afterwards (earned by the chapter itself, never by previewing another);
5. hears no invented facts (every claim is checked against a researched source).

The evening before, the listener was shown this card. Its summary is a promise: the question the chapter takes on, its angle, and why it follows from them. Your outline keeps every part of it. Where the research does not bear out something the summary expected, the outline says what the research does show on that question; it never quietly swaps the question for another.

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

The scout's sketch of the territory, made before the research:

<map>
{{map}}
</map>

The research ledger: the ideas the chapter was researched to land, each with how its research came back. A row that came back partial or open has a gap: plan around what its entries do give, and never count on what it did not find.

<ledger>
{{ledger}}
</ledger>

The dossier. Every fact the chapter tells comes from one of its numbered entries, each tagged with its rows, its kind, its source tier and, for quotes, whether it is verbatim:

<dossier>
{{dossier}}
</dossier>

Source tiers:

{{source_tiers}}

Write the outline. Keep every field as short as it is asked for: the writers need a plan, not prose.

## The chapter

- **angle**: the one claim the chapter argues, in two or three sentences: the card's angle, sharpened by what the research found. A claim is something a person could disagree with, not a topic.
- **why**: two or three sentences: why this angle keeps the card's promise for this listener, and what it leaves out.
- **through_line**: the one question or tension that carries the listener from the first section to the last, in one or two sentences: the summary's question. The opening asks it, each section after moves it on with something new, and the last answers it; no later section is planned to ask it over again. The chapter is an argument with a shape, not a tour.
- **closing_idea**: what the last lines leave the listener with, in one or two sentences: an image or an open question that keeps working after the audio stops. It comes after the chapter's answer and does not put the through-line again in other words. It may call back to the opening scene. The chapter stands alone, so it never previews another.
- **listener_words**: one to three of the listener's own phrases, copied exactly from their words above, for the opening to echo once the scene has begun. Their words only, never a paraphrase. Empty if there are none.

## The sections

{{min_sections}} to {{max_sections}} sections, numbered from 1, in the order the listener hears them. You decide how many: one idea each, so four strong sections beat six thin ones.

Sizes, at {{words_per_minute}} words a minute. Code checks these and rejects an outline that breaks one:
- The targets add up to about {{target_words}} words, and never less than {{min_words}} or more than {{max_words}}.
- Section 1 is at least {{opening_min_words}} words ({{opening_min_minutes}} minutes); no other section is under {{section_min_words}} words ({{section_min_minutes}} minutes).
- They need not be equal. Give the idea with the richest entries the most room, and a row whose research came back thin less.

Every ledger row ({{row_ids}}) is landed by exactly one section; a section may land one row, two, or none (an opening that sets the scene, an ending that draws the answer together). A parked idea is not a row and gets no section.

For each section:
- **n**: its number.
- **title**: two to five words, shown on the page as the section's marker while it plays. It names the section without giving away its turn.
- **lands**: the one idea the listener holds when the section ends, in one sentence: a claim or a move in the argument that the section's entries bear out, something they could repeat to a friend. "Roman sea concrete grows stronger as seawater gets into it" is an idea; "Roman concrete" is a topic.
- **sets_up**: what it plants that a later section pays off, in one sentence: a concrete thing the listener will meet again (a detail of a scene, a claim someone made, a question), never the through-line or the angle in other words. For the last section: which earlier set-up it pays off.
- **hand_off**: the question or image in its last lines that pulls the listener into the next section, in one sentence: a question left open, never a promise of what comes next. For the last section: how it ends on the through-line.
- **target_words**: a number.
- **notes**: how to tell it, in two to four sentences: the scene it opens on, the order of its moves (each with its entry numbers), what to hold back for a later section. A move tells the listener something from an entry: a scene with every detail its entries give, a person, a number, a quotation. Told aloud, a move carries about half a minute, a full scene about a minute; count the moves against the length, and where they fall short, plan the section shorter rather than padded.
- **rows**: the ledger row ids it lands.
- **entries**: the numbers of the dossier entries it draws on (its rows' entries it uses, and any others), at least one. An entry is planned in one section; a later section may recall its fact in a clause.

## Section 1, the opening

Its first lines are a scene: something that happened, in a place, to someone, from the strongest material in the dossier that does not answer the question. Within the first minute the listener also knows what they are listening to, how it grows from what they asked or said (tied to their words, after the scene has begun), and the question the chapter will chase. It asks that question and holds back the answer. Say in its notes where each of these comes.

## Rules for every section

- Plan only what the entries state. Name no date, number, name or place that no entry gives, and plan a quotation only from an entry marked verbatim: yes. A second-hand entry (a work quoted or reported by a page that is not the work) is planned as that page's report.
- What a section's argument rests on comes from tier 1 or 2 entries; a tier 4 entry is color only. An entry tagged corrected gives the corrected fact.
- A tension entry that holds the strongest objection to a section's idea is planned in that section, where the listener would raise it.
- The order is an argument: each section needs the one before it. Somewhere the listener's first picture is overturned or deepened; say in that section's notes where the turn comes.
