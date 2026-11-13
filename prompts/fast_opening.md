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
- Tell; do not circle. Every fact comes from a scout entry and is marked, and the section is carried by what it tells: the plan's moves in order, each scene with every detail its entries give. Ask the chapter's question once, inside the first minute; it may come back once, in a sentence that turns a scene toward it, and no more. State the through-line's tension once. After the tie to the listener's words, a paragraph with no marked sentence in it is framing, unless it is the hand-off: fold what it says into a sentence beside the fact it bears on, or leave it out. If the plan's moves are told and the length is not reached, tell more from the scout, a scene or an entry that is not a later section's job in the outline; if there is no more, stop at the low edge of the length.
- The opening does not answer the question: plant what the plan says this section sets up, once each, and leave it unexplained. The deeper research is still under way: do not reach past the scout, and do not promise specifics ("we will see exactly how...") that a later section would then owe. A question left open for the listener is a promise too: beyond the chapter's question, leave open only the plan's hand-off.
- Where an entry is second-hand (a written work quoted or reported by a page that is not the work), say once, in plain words, which page quotes or reports it, and give what the work says or is about only as that page's account, there and in every later sentence, never as your own statement.
- End on the plan's hand-off as a place to rest (rule 9). The next section may arrive after a pause.

Length: about {{target_words}} words. Stay within 10% of that.

Before you finish, check: the first paragraph is short and stands alone; after the tie to the listener's words, every paragraph but the hand-off has a marked sentence; every factual sentence has its [Dn] marker, and that entry says what the sentence says; every direct quote is the exact words of an entry marked verbatim: yes; the last lines would hold if nothing followed for a minute.

Output only the section's text. No preamble, no title, no notes after it.
