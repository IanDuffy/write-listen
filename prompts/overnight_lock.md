# Prompt: lock one section (Overnight Chapters; stage fast_lock's settings, structured output)

Inputs:
- {{n}}, {{total}}, {{title}}: the section being locked, the section count, its marker title
- {{draft}}: the latest draft of section n
- {{mode}}: fast_lock_paired.md (the draft of n+1 beside it) or fast_lock_final.md (the last section,
  read against the whole chapter). Overnight never locks solo.
- {{plan}}

Notes the listener added after the plan was locked (honour them; a section that ignores one that bears on it is not ready):

<notes>
{{notes}}
</notes>: section n's part of the outline
- {{rows}}: the ledger rows section n must land, with how their research came back, and any row an
  earlier section left thin or missing
- {{entries}}: the whole dossier (overnight/write.py Dossier: row L00 stands for all of it)
- {{target_words}}: section n's length
- {{notes}}: notes the listener added after the plan was locked, while research ran ("(none yet)")
- {{note_entries}}: supplied by faststart/sections.py and not used here

Output: JSON per faststart/sections.py SCHEMA: text, next_text, moved, rows, cut, title, corrections.

System prompt: overnight_context.md. Sent after overnight_earlier.md and one block per locked section.
The overnight counterpart of fast_lock.md: keep the two in step. What differs: nothing is narrated
the moment it is returned (the whole chapter is checked once against its card's summary afterwards,
overnight/promise.py), listener notes are only those added while research ran, and no corrections (the research was finished
before the first draft). The first line must stay "Lock section N of M" (tests read it).
---
Lock section {{n}} of {{total}}: {{title}}. You are its editor, seeing the draft for the first time. You did not write it and owe it nothing. The text you return for section {{n}} is final: after you, the chapter is only checked as a whole against what the listener was promised. Every word you write goes to the listener, so every fact in your wording must come from an entry below.

The draft of section {{n}}:

<draft n="{{n}}">
{{draft}}
</draft>

{{mode}}

Section {{n}}'s plan from the outline:

<plan>
{{plan}}
</plan>

The ideas section {{n}} must land, from the research ledger, and how the research on each came back:

<rows>
{{rows}}
</rows>

The dossier entries. They are the only source of facts:

<entries>
{{entries}}
</entries>

Read section {{n}} as the listener would, straight after the sections above, then as a fact-checker, sentence by sentence.

As a listener:
- Its job. Does it land what its plan says it lands, as far as the entries bear it out, set up what it sets up, and end on its hand-off (rule 9)? Does its first sentence pick up the thread without recapping (for section 1: open on its scene, and within the first minute say what this is, how it grows from what the listener asked, and the question the chapter chases)? Where the entries do not bear out what the plan expected, the section says what they do show and never asserts the plan's expectation.
- What the listener was promised. The chapter as a whole keeps the outline's promise; this section does its part of it and does not wander from it.
- Repetition of an earlier section. The listener has heard everything above. Cut any point, example, image or quotation they have already been given, and list it in "cut" with the section it repeats. That includes a sentence that only puts the chapter's question or the through-line again in other words, and a scene or phrase brought back as a refrain (rule 8): keep the sentence that carries the new finding and cut the one beside it that only says it again. Not repetition: the hand-off's question, a fact recalled in a clause, and, in the last section, the question named once as it is answered and the one callback.
- Cut what slows it without earning it: throat-clearing, recaps, a second example where one would do.
- Add what is missing, from the entries only: a step in the argument, the scene an idea should arrive with, a line where the listener would lose the thread, the strongest objection a tension entry holds against what the section lands (once, where the listener would raise it, with what the entries answer or what an entry says has not been shown).
- Pacing and the ear: a passage that drags or rushes, a paragraph doing two things, long sentences, lists, asides, a hard name not introduced slowly.
- Tone: warm and plain, the same voice as the sections above. Cut lecturing, performed enthusiasm and hedging.
- Held beats: a [pause] belongs after a turn, a striking quote or a question left hanging. At most one in this section, on a line of its own; none if the section before ended on one.

As a fact-checker, for every sentence that says something about the world or about a text, marked or not:
- It carries a [Dn] marker. A factual claim without one is tied to the entry that supports it, or cut. A sentence about what a text says, leaves out, or how it is built is a factual claim.
- The cited entries say what the sentence says, not something weaker, stronger or different. A tension or connection entry can back an interpretation presented as one, never a statement of what a text says or what happened. Reword to what the entry supports, or cut.
- Attribution: the right author, speaker and translator. Never credit wording to someone else, or to unnamed "older translations" or "scholars".
- Dates, intervals and the order of events follow from the entries.
- What the argument rests on is tier 1 or 2. A tier 4 entry is color only.
- Every direct quote is the exact words of an entry marked verbatim: yes, attributed aloud before it begins. A quote from any other entry becomes a paraphrase without quotation marks.
- Where you know a source says otherwise than the draft, cut or soften the sentence. Never add a fact the entries do not contain.
- Nothing speaks of how the chapter was made (rule 10). Where an entry contradicts what a section above said, state the entry's fact here without comment if this section touches it.

Change what needs changing and nothing else: where the draft is right, keep its words. Keep section {{n}} within about 15% of {{target_words}} words. A section whose rows came back thin may stay short; never lengthen it with anything that is not new to the listener.

Return JSON with these fields:
- "text": the final text of section {{n}}, in full: plain paragraphs with their markers. No segment marker, no title, no notes.
- "rows": one item for each ledger row listed above. "id": the row. "coverage": covered (the final text lands the idea with its concrete material), thin (the idea is there, without the material that makes it land) or missing (it is not in the final text). "note": one line saying why; for thin or missing, whether a later section should take it up.
- "cut": one item for each passage you removed outright from a draft above (not a sentence you only tightened). "text": the passage word for word as it stood in the draft it came from. "reason": why, in a few words; for a repetition, of which section.
- "title": a new title for this section's marker on the listener's page, six words at most, only if the outline's title no longer fits what the section now says; otherwise an empty string.
- "corrections": an empty list.
