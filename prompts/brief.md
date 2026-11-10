# Prompt: brief (stage 5)

Inputs:
- {{angle}}: 03_angle.md
- {{dossier}}: 04_dossier.md
- {{style_guide}}: prompts/style_guide.md
- {{target_words}}, {{target_minutes}}, {{min_segments}}, {{max_segments}}: config.yaml

Output: 05_brief.md. Ian edits it by hand before drafting. The draft stage reads
the '## Segment N: Title' headings and the 'Target words:' and 'Claims:' lines,
so keep those exact.
---
You are the editor commissioning an audio chapter. Write the brief a writer will draft from, one segment at a time. The writer sees this brief, the dossier, and the segments already written, and nothing else. A good brief makes every segment's job unmistakable.

The chosen angle:

<angle>
{{angle}}
</angle>

The research dossier. Every factual claim in the chapter must come from it, cited by its number:

<dossier>
{{dossier}}
</dossier>

The house style guide:

<style_guide>
{{style_guide}}
</style_guide>

The chapter is about {{target_words}} words, about {{target_minutes}} minutes aloud, in {{min_segments}} to {{max_segments}} segments of roughly equal length. One narrator.

Write the brief in markdown with exactly these sections, in this order:

# Brief: the chapter's title

## Thesis
The one claim the chapter argues, in one or two sentences, and what the listener should believe or see differently by the end.

## Listener
A curious non-specialist. They may have heard the episode. They expect the primary texts to be over their head; the chapter should prove otherwise by reading those texts with them, slowly. Add anything specific to this angle: what they probably already think, and what will surprise them.

## Opening hook
The first thirty seconds, described concretely: the scene, image or question that opens the chapter, and the dossier claims it uses.

## Closing line
The last sentence or two, written out, and what it should leave the listener with.

## Style guide for the ear
What this chapter adds to the house style guide: its recurring images, the names that need introducing and how, and any tone notes. Don't copy out the house rules; the writer sees the house guide alongside this brief, and the house guide wins where the two disagree.

## Arc
Three or four sentences on how the segments build: where the turn comes, and where the chapter is hardest and needs the most care.

## Segment 1: a short title
- Purpose: what this segment does for the argument, in one sentence.
- Target words: a number
- Claims: C3, C7, C12 (each number listed separately on this one line, no ranges)
- Beats: the moves of the segment in order, as short sentences.
- Opens with: how it picks up from the previous segment (for segment 1, the hook).
- Hands off to: the question or image that pulls the listener into the next segment.

## Segment 2: a short title
...and so on, through the last segment.

Rules:
- The segment targets add up to about {{target_words}}.
- Every segment lists the dossier claims it must carry, by number, and only numbers that exist in the dossier. Spread the claims so no segment is a pile of facts and none is all argument.
- Each thinker is introduced once, in one segment, with a scene or image from the dossier. Later segments can return to them.
- Don't plan any fact the dossier does not contain. If the angle needs one that is missing, say so in a final section, "## Gaps", rather than inventing it.
