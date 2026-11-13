# Prompt: write the opening (Fast Start, stage fast_opening)

Inputs:
- {{total}}: the section count
- {{plan}}: section 1's part of the outline
- {{listener_words}}: the listener's own phrases, from the outline, for the first minute
- {{scout}}: 01_scout.md without its entries: the territory and the two or three scenes
- {{entries}}: the scout's entries as the dossier has them (checked, numbered D01...)
- {{notes}}: listener_notes.md so far (what was said during the wait), or "(none yet)"
- {{target_words}}: the opening's length
- {{first_words}}: the most words the first paragraph may have; it is narrated alone, so
  that the first audio is ready as soon as possible (fast.narration.first_piece_max_chars)

System prompt: fast_context.md. The fast counterpart of draft.md for segment 1. The opening
is written from the scout alone, while the research for the later sections is still out;
whatever it promises, those sections have to deliver from research nobody has seen yet.
---
Write section 1 of {{total}}: the opening. It is the chapter's introduction and its first section in one.

Its plan from the outline:

<plan>
{{plan}}
</plan>

The listener's own words, from the conversation that led to this chapter:

<listener_words>
{{listener_words}}
</listener_words>

What the scout found: the territory in brief, and the scenes worth telling.

<scout>
{{scout}}
</scout>

The scout's entries. They are all the research there is so far, and the only source of facts for this section:

<entries>
{{entries}}
</entries>

What the listener has said since the topic was set (rule 10):

<listener_notes>
{{notes}}
</listener_notes>

How to write it:

- Lead with the story. Open inside the scene the plan names: a place, a person doing something, told so the listener can see it. Then ask the question the chapter will answer, plainly.
- Orient within the first minute. By then the listener knows what they are listening to, that it grows from what they asked about (in their own words where those fit naturally, a sentence or two, without quotation marks: their words are not a quotation from a source), and the question the chapter will chase. No "welcome to", no stock formula.
- Make the first paragraph short, {{first_words}} words at most, and complete in itself. It is narrated on its own so the audio can start while the rest is being voiced.
- Few facts, each one from a scout entry and marked. The opening's work is the scene and the question, not the evidence. The deeper research is still under way: do not reach past the scout, and do not promise specifics ("we will see exactly how...") that a later section would then owe. Open the questions the plan says this section sets up, and leave them open.
- End on the plan's hand-off as a place to rest (rule 9). The next section may arrive after a pause.

Length: about {{target_words}} words. Stay within 10% of that.

Before you finish, check: the first paragraph is short and stands alone; every factual sentence has its [Dn] marker, and that entry says what the sentence says; every direct quote is the exact words of an entry marked verbatim: yes; the last lines would hold if nothing followed for a minute.

Output only the section's text. No preamble, no title, no notes after it.
