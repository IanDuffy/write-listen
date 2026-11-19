# The debrief summary (demo/interviewer.py summarize; written to listeners/<lid>/study/debrief_summary.md)

Spec: docs/tester-demo.md, "What it saves": a Claude summary of one tester's debrief in the tester's words, with
notable quotes and timestamps. Since Ian's notes of 2026-11-16 the debrief is a conversation around six topics,
not five fixed questions, so the summary is organised by those topics (demo/interviewer.py TOPICS;
prompts/interviewer_debrief.md). Since his notes of 2026-11-18 the debrief is three or four questions, so most
topics will read "Didn't come up", and the summary opens with what the tester did ({{did}}: the same account
the interviewer was given, from the tester's own records), so Ian reads what each tester did beside what they
said. The summary of his own debrief had said "He did not cut in during the piece" when he had heard none of
it: the account is the only source for what they did. `wl study report` reads these across testers, so the
headings are fixed. Quotes must be the tester's own words from the transcript, never paraphrase dressed as a
quote. Inputs: {{person}}, {{title}}, {{summary}}, {{did}}, {{transcript}} (the debrief, one line per turn,
"[m:ss] who: text", times from the session's start), {{intro}} (the intro's transcript, the same way; it may
be "(none)").
---
You are summarising one person's debrief after they tried something a voice made for them: a short audio piece, which they could interrupt with questions. The person who built it will read this to learn from it. Below are what they did, from the app's own record, and the interviewer's conversation with them.

Their name: {{person}}
The piece: "{{title}}": {{summary}}

What they did (the app's record: accurate, and the only source for what they did or heard):
{{did}}

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

## What they did
## What stood out
## Would they use it, and when
## Where they imagine it going
## The voice
## Asking questions
## Confusing, or got in the way
## Anything else they raised
## Notable quotes

Under "What they did": the record above as two to four plain sentences, nothing added and nothing left out, no quotes.
Under each of the next six headings: what they said about it in two to four sentences, as close to their own words as possible, wherever in the conversation they said it, then one or two short direct quotes in quotation marks with the time, like: "it felt like someone was talking just to me" [3:12]. The debrief is short, so most of these won't have come up: write "Didn't come up." If the interviewer asked and they didn't really answer, say so.
Under "Asking questions": how their own questions mid-piece went, if the record lists any and they spoke about them. If the record says they heard none of the piece, say that, not that they chose not to ask.
Under "Confusing, or got in the way": every bug, glitch, misunderstanding or moment they didn't know what to do, one per line with its time, or "None mentioned."
Under "Anything else they raised": their answer to "Is there anything I didn't ask that I should have?" if it was asked (it is optional: a debrief without it is complete), and anything outside the six topics, briefly.
Under "Notable quotes": three to six of their most telling lines, word for word, each with its time.

Rules: quote only words the person actually said in the transcript, exactly. Never put the interviewer's words in their mouth. What they did comes from the record alone: never infer it from the interviewer's questions. Don't score, rate or judge them or the piece. Plain and short.
