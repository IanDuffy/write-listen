# Prompt: brief (stage 5)

Inputs:
- {{angle}}: 03_angle.md
- {{dossier}}: 04_dossier.md, entries numbered D01, D02, ... and tagged by ledger row
- {{ledger}}: the active rows of ledger.json, with each row's research status from stage 04b
- {{connections}}: the cross-row links from stage 04b
- {{corrections}}: the dossier entries a verify agent marked corrected (code also lists them at the end of the brief)
- {{style_guide}}: prompts/style_guide.md
- {{source_tiers}}: prompts/source_tiers.md
- {{target_words}}, {{target_minutes}}, {{min_segments}}, {{max_segments}}: config.yaml

Output: 05_brief.md. Ian edits it by hand before drafting. The draft and
coverage stages read the '## Segment N: Title' headings and the 'Target words:',
'Ledger:' and 'Dossier:' lines, so keep those exact.
---
You are the editor commissioning an audio chapter. Write the brief a writer will draft from, one segment at a time. The writer sees this brief, the dossier, and the segments already written, and nothing else. A good brief makes every segment's job unmistakable.

The chosen angle:

<angle>
{{angle}}
</angle>

The ledger: the ideas this chapter must land, ranked by importance to the angle, each with what the research found:

<ledger>
{{ledger}}
</ledger>

Links between the ideas that the research found, for transitions and the chapter's through-line:

<connections>
{{connections}}
</connections>

The research dossier. Every factual claim in the chapter must come from it, cited by its number. Each entry is tagged with its ledger row, its kind (claim, material, quote, tension, connection) and its source tier:

<dossier>
{{dossier}}
</dossier>

Source tiers:

{{source_tiers}}

Facts the episode got wrong, which the chapter must give in their corrected form:

<corrections>
{{corrections}}
</corrections>

The house style guide:

<style_guide>
{{style_guide}}
</style_guide>

The chapter is about {{target_words}} words, about {{target_minutes}} minutes aloud, in {{min_segments}} to {{max_segments}} segments of roughly equal length. One narrator.

Write the brief in markdown with exactly these sections, in this order:

# Brief: the chapter's title

## Through-line
The one question or tension that carries the listener from the first segment to the last, in one or two sentences. Then, in a sentence each, how each ledger row serves it, in the order the chapter takes them. The chapter is an argument with a shape, not a tour of the rows.

## Thesis
The one claim the chapter argues, in one or two sentences, and what the listener should believe or see differently by the end.

## Listener
A curious non-specialist. They may have heard the episode. They expect the primary texts to be over their head; the chapter should prove otherwise by reading those texts with them, slowly. Add anything specific to this angle: what they probably already think, and what will surprise them.

## Opening hook
The first thirty seconds, described concretely: the scene, image or question that opens the chapter, and the dossier entries it uses.

## Closing line
The last sentence or two, written out, and what it should leave the listener with.

## Style guide for the ear
What this chapter adds to the house style guide: its recurring images, the names that need introducing and how, and any tone notes. Don't copy out the house rules: the writer sees the house guide alongside this brief. Where your section disagrees with the house guide, yours wins, so only write a rule that differs if you mean to override it.

## Arc
Three or four sentences on how the segments build: where the turn comes, and where the chapter is hardest and needs the most care.

## Segment 1: a short title
- Purpose: what this segment does for the through-line, in one sentence.
- Target words: a number
- Ledger: L01, L03 (the ledger rows this segment lands or advances)
- Dossier: D03, D07, D12 (the entries the segment must use; each number listed separately on this one line, no ranges)
- Beats: the moves of the segment in order, as short sentences.
- Opens with: how it picks up from the previous segment (for segment 1, the hook).
- Hands off to: the question or image that pulls the listener into the next segment.

## Segment 2: a short title
...and so on, through the last segment.

Rules:
- Every ledger row appears on at least one segment's Ledger line. Higher-ranked rows get more room. A row that is the main business of a segment should land there, not be spread thinly across several.
- For each row, the segment that lands it uses the row's tellable material (its material entries) and, where it makes the argument stronger, its tension.
- The segment targets add up to about {{target_words}}.
- List only entry numbers that exist in the dossier. Spread the entries so no segment is a pile of facts and none is all argument.
- What a segment's argument rests on comes from tier 1 or 2 entries. Tier 4 entries are color only.
- Plan quotes only from entries marked verbatim: yes. Other entries are paraphrased.
- Use each corrected fact in its corrected form; never plan the episode's version.
- Each thinker is introduced once, in one segment, with a scene or image from the dossier. Later segments can return to them.
- Use the connections for the hand-offs between segments where they fit.
- Don't plan any fact the dossier does not contain. If the angle needs one that is missing, including anything a ledger row's research left open, say so in a final section, "## Gaps", rather than inventing it.
