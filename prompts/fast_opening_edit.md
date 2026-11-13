# Prompt: edit the opening (Fast Start, stage fast_opening_edit)

Inputs:
- {{total}}: the section count
- {{draft}}: the opening as drafted (06_segments/drafts/seg01.d1.md)
- {{plan}}: section 1's part of the outline
- {{listener_words}}: the listener's own phrases, from the outline
- {{entries}}: the scout's entries as the dossier has them
- {{notes}}: listener_notes.md so far, or "(none yet)"
- {{target_words}}, {{first_words}}: the opening's length; the most words its first paragraph may have

System prompt: fast_context.md. One pass, in a fresh context, and its output is narrated at
once: the listener is waiting for the first audio. It does the work of edit_seed.md and
factcheck.md for this one section and returns final text, not notes. Code then checks the
text (faststart/checks.py section_problems, scout entries only); fast_fix.md handles what it finds.
---
Edit section 1 of {{total}}, the opening, and return its final text. You are its editor, seeing it for the first time. You did not write it and owe it nothing. It is narrated the moment you return it, and nobody reads it after you: every word you write goes to the listener, so every fact in your wording must come from an entry below.

The draft:

<draft>
{{draft}}
</draft>

Its plan from the outline:

<plan>
{{plan}}
</plan>

The listener's own words, from the conversation that led to this chapter:

<listener_words>
{{listener_words}}
</listener_words>

The scout's entries: all the research there is so far, and the only source of facts for this section.

<entries>
{{entries}}
</entries>

What the listener has said since the topic was set (rule 10):

<listener_notes>
{{notes}}
</listener_notes>

Read the draft as the listener would, start to finish, then as a fact-checker, sentence by sentence.

As a listener:
- The start. Is the listener inside a scene from the first lines? Within the first minute, do they know what they are listening to, that it grows from what they asked about, and the question the chapter will chase? Cut any "welcome to", any stock formula, anything that delays the question.
- The first paragraph is at most {{first_words}} words and complete in itself: it is narrated alone so the audio can start. Split or tighten it if it is longer.
- Cut what slows it without earning it: throat-clearing, a second example where one would do, evidence that belongs to a later section's job in the outline.
- Promises. The later sections are written from research nobody has seen yet. Cut or soften any promise of specifics; the questions the plan sets up stay open.
- The ear: long sentences, lists, asides, a hard name not introduced slowly, anything a narrator would stumble on.
- Tone: warm and plain. Cut performed enthusiasm, hedging and lecturing. The tie back to what the listener said is a sentence or two, and never thanks or flatters.
- The end. The last lines land the plan's hand-off and stand alone (rule 9): a thought landed and a question left open, complete if nothing followed for a minute. Cut any announcement of what comes next.
- Held beats: at most one [pause] in this section, after a turn or a question left hanging, on a line of its own.

As a fact-checker, for every sentence that says something about the world or about a text, marked or not:
- It carries a [Dn] marker. A factual claim without one is tied to the entry that supports it, or cut.
- The cited entry says what the sentence says, not something weaker, stronger or different. Reword to what the entry supports.
- Only the entries above may be cited.
- Every direct quote is the exact words of an entry marked verbatim: yes, attributed aloud before it begins to the right speaker and translator. A quote from any other entry becomes a paraphrase without quotation marks; never credit wording to someone else or to unnamed "scholars". The listener's own words are not a quotation from a source: where the draft has them in quotation marks, take the marks off and keep the words.
- What the argument rests on is tier 1 or 2. A tier 4 entry is color only.
- Where you know a source says otherwise than the draft, cut or soften the sentence. Never add a fact the entries do not contain.

Change what needs changing and nothing else: where the draft is right, keep its words. Keep the length within 10% of {{target_words}} words.

Output only the final text of the section: plain paragraphs with their markers. No preamble, no notes, no list of changes.
