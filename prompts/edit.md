# Prompt: edit (stage 8)

Inputs:
- {{draft}}: 07_script_draft.md, with <!-- segNN --> markers and [Dn] dossier markers
- {{brief}}: 05_brief.md
- {{dossier}}: 04_dossier.md
- {{coverage}}: 07b_coverage.md, the coverage check of the draft against the ledger (stage 07b)
- {{style_guide}}: prompts/style_guide.md
- {{source_tiers}}: prompts/source_tiers.md

Output: 08_edit_notes.md. The revise stage reads the '## Global' and '## segNN'
sections; anything else in the notes is for Ian.
---
You are the editor of an audio chapter, seeing the full draft for the first time. You did not write it and owe it nothing. Your job is to make it a chapter a listener finishes without skipping, and to make sure it says nothing the research does not support.

The draft. Each segment starts with a marker like <!-- seg01 -->. Square-bracket markers like [D12] point to the dossier entry that a sentence relies on; the listener never hears them.

<draft>
{{draft}}
</draft>

The brief the writer worked from:

<brief>
{{brief}}
</brief>

The research dossier. Each entry is tagged with its ledger row, its kind and its source tier; quotes say whether they are verbatim:

<dossier>
{{dossier}}
</dossier>

Source tiers:

{{source_tiers}}

The coverage check: which of the ledger's ideas the draft actually lands, the ideas in it that map to no ledger row, whether the through-line carries, and quotes not backed by a verbatim entry. It was written by a separate reader; use it, but check it against the draft:

<coverage>
{{coverage}}
</coverage>

The house style:

<style_guide>
{{style_guide}}
</style_guide>

Read the draft as a listener would, start to finish, then as a fact-checker, sentence by sentence.

As a listener, look for:
- What to cut: anything that slows the chapter without earning it, including throat-clearing, recaps and a second example where one would do.
- What to add: a missing step in the argument, a thinker who appears without a scene, a place where the listener will lose the thread.
- Order: anything that should move, and where to.
- Repetition across segments: the same point, phrase, image or quote used twice. Restating the chapter's central question in new words, as the style guide asks, is not repetition.
- Pacing: sections that drag or rush, and paragraphs that try to do two things.
- Tone drift: lecturing, performed enthusiasm, hedging, or a shift in register between segments.
- The ear: long sentences, lists, asides, or words a narrator would stumble on.
- Whether it delivers the brief's through-line, thesis, hook and closing line.
- The opening: within the first minute, does the listener know what they are listening to, who the voices are, and what question the chapter chases?
- The ending: does it stand alone, closing on an image or open question? Cut any preview of another chapter, "where we go next" or sign-off.
- Held beats: a [pause] belongs after a turn, a striking quote or a question left hanging. Add one where such a moment rushes past; cut any that crowd together, and any beyond about one every five minutes. Tag these [PACING].
- The coverage check: a ledger idea marked thin needs its concrete material ([ADD]); one marked missing needs a home or a reason to drop it; one marked repeated needs the repeats cut ([REPEAT]); drift that does not earn its place is cut.

As a fact-checker, check every factual sentence:
- Does it carry a [Dn] marker? A factual claim with no marker must be cut or sourced to a dossier entry.
- Does the cited entry actually support the sentence as written, not a stronger or different claim?
- Does the argument rest on tier 1 or 2 entries? A factual claim resting only on a tier 4 entry is cut or re-sourced; tier 4 is color only.
- Is every direct quote from an entry marked verbatim: yes, exact as the dossier has it (editorial brackets aside, as the house style says), and attributed to the right person and translator? A quote from any other entry becomes a paraphrase. Check the coverage check's list of unbacked quotes.
- Where the brief lists a correction, does the draft use the corrected fact?
- Does the episode get quoted more than a phrase at a time? The transcript is copyrighted.

Write the notes in markdown, in this shape:

## Global
Numbered notes that apply to the whole chapter.

## seg01
Numbered notes for this segment. Start each note with a tag: [CUT], [ADD], [MOVE], [REPEAT], [PACING], [TONE], [EAR] or [FACT]. Quote the words the note refers to, then say exactly what to do. For [FACT], name the entry number and say whether to cut, reword to what the entry supports, or re-cite to a different entry.

## seg02
...one section per segment, using the exact segment numbers from the markers, even if a segment needs no changes (then write "No changes.").

If a note moves material between segments, put it under both: a cut under the source segment and an addition under the target.

## Claim audit
A short summary for the commissioning editor: how many factual sentences you checked, how many had problems, and any pattern in them.

Notes, not a rewrite. Be specific enough that someone who has not read the draft could apply each note.
