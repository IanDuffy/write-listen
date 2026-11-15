# Prompt: does the chapter keep its promise? (Overnight Chapters, stage overnight_promise, structured output)

Acceptance check 7: the finished chapter matches its card's summary. A judge in a fresh context reads
the written chapter against the Up next card the listener saw the evening before. If it drifted,
each section it names gets one revision (overnight_revise.md); nothing checks again.

Inputs:
- {{title}}, {{overview}}, {{summary}}: the card (00_source/up_next.json)
- {{words}}: the listener's own words the plan came from, or "(none)"
- {{through_line}}: the outline's through-line
- {{total}}: the section count
- {{script}}: the chapter as the listener hears it, one <section n="N" title="..."> block each

Output: JSON per overnight/promise.py SCHEMA: keeps, points, drift, sections.
---
The evening before, a listener was shown a card for the audio chapter they would hear in the morning. Overnight the chapter was researched and written. You are checking, before it is recorded, whether it is the chapter the card promised. You have not seen how it was made.

The card:

<card>
Title: {{title}}
Overview: {{overview}}
Summary: {{summary}}
</card>

The listener's own words the plan came from, if any:

<listener_words>
{{words}}
</listener_words>

The through-line the chapter was planned around: {{through_line}}

The chapter, {{total}} sections, as the listener will hear it:

<script>
{{script}}
</script>

Break the summary into the things it tells the listener to expect: the question the chapter takes on, the angle it argues, why it follows from them, and anything else it names (a person, a work, an example, a place). For each, find where the chapter keeps it.

The chapter keeps its promise if a listener who read the card would recognise this as that chapter: it takes on that question, argues that angle or says plainly where the research led elsewhere on it, and shows the link to them the card named. It need not use the card's words, follow its order, or give everything it mentions equal time. It drifts if it answers a different question, argues a different angle without saying so, leaves out something the summary names as central, or never makes the link to the listener the card promised. Judge the promise only: not the prose, not the facts, not the length.

Return JSON:
- "keeps": true if the chapter keeps its promise, false if it drifted.
- "points": one item for each thing the summary tells the listener to expect. "point": it, in a few words. "kept": true or false. "where": the section number(s) that keep it and how, in one line; or, if not kept, what the chapter does instead.
- "drift": one line for each way the chapter drifted from the summary. An empty list if it keeps its promise.
- "sections": only if it drifted, one item for each section that should change to keep the promise, at most three. "n": the section number. "fix": what this section should do differently, in two or three sentences, concrete enough for a writer to act on in one revision: what to add, reframe or bring forward, and where. A fix may only use what the chapter's own material can carry: never ask for a fact the chapter does not already touch. An empty list if it keeps its promise.
