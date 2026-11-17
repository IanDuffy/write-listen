# The debrief summary (demo/interviewer.py summarize; written to listeners/<lid>/study/debrief_summary.md)

Spec: docs/tester-demo.md, "What it saves": a Claude summary of one tester's debrief in the tester's words, with
notable quotes and timestamps. Since Ian's notes of 2026-11-16 the debrief is a conversation around six topics,
not five fixed questions, so the summary is organised by those topics (demo/interviewer.py TOPICS;
prompts/interviewer_debrief.md). `wl study report` reads these across testers, so the headings are fixed. Quotes must be the tester's own words from the transcript, never
paraphrase dressed as a quote. Inputs: {{person}}, {{title}}, {{summary}}, {{heard}}, {{moments}} (their questions
mid-chapter), {{transcript}} (the debrief, one line per turn, "[m:ss] who: text", times from the session's
start), {{intro}} (the intro's transcript, the same way; it may be "(none)").
---
You are summarising one person's debrief after they tried an audio piece made for them, so the person who made it can learn from it. Below are what they heard and the interviewer's conversation with them.

Their name: {{person}}
What they heard: "{{title}}": {{summary}}
{{heard}}
Moments they stopped it to ask something: {{moments}}

The intro (before they started):
<intro>
{{intro}}
</intro>

The debrief:
<transcript>
{{transcript}}
</transcript>

Write the summary in Markdown with exactly these headings, in this order:

# Debrief: {{person}}

## What stood out
## Would they use it, and when
## Where they imagine it going
## The voice
## Asking questions
## Confusing, or got in the way
## Anything else they raised
## Notable quotes

Under each of the first six headings: what they said about it in two to four sentences, as close to their own words as possible, wherever in the conversation they said it, then one or two short direct quotes in quotation marks with the time, like: "it felt like someone was talking just to me" [3:12]. If it never came up, write "Didn't come up." If the interviewer asked and they didn't really answer, say so.
Under "Asking questions": how their own questions mid-piece went (listed above), or whether anything made them want to ask.
Under "Confusing, or got in the way": every bug, glitch, misunderstanding or moment they didn't know what to do, one per line with its time, or "None mentioned."
Under "Anything else they raised": their answer to "Is there anything I didn't ask that I should have?" and anything outside the six topics, briefly.
Under "Notable quotes": three to six of their most telling lines, word for word, each with its time.

Rules: quote only words the person actually said in the transcript, exactly. Never put the interviewer's words in their mouth. Don't score, rate or judge them or the piece. Plain and short.
