# The debrief summary (demo/interviewer.py summarize; written to listeners/<lid>/study/debrief_summary.md)

Spec: docs/tester-demo.md, "What it saves": a Claude summary of one tester's debrief, answers to each core
question in the tester's words, with notable quotes and timestamps. `wl study report` (part B) reads these
across testers, so the headings are fixed. Quotes must be the tester's own words from the transcript, never
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

## 1. What it was like, from the start
## 2. Most into it, and where they drifted or something felt off
## 3. Stopping to ask
## 4. How they'd describe it to a friend
## 5. Would they listen tomorrow, and what would make the difference
## Anything else they raised
## Notable quotes
## Bugs and confusions

Under each numbered heading: their answer in two to four sentences, as close to their own words as possible, then one or two short direct quotes in quotation marks with the time, like: "it felt like someone was talking just to me" [3:12]. If the interviewer never asked that question, write "Not asked." If they didn't really answer, say so.
Under "Anything else they raised": their answer to "Is there anything I didn't ask that I should have?" and anything else they brought up, briefly.
Under "Notable quotes": three to six of their most telling lines, word for word, each with its time.
Under "Bugs and confusions": anything that went wrong or confused them (technical problems, misunderstandings, moments they didn't know what to do), one per line, or "None mentioned."

Rules: quote only words the person actually said in the transcript, exactly. Never put the interviewer's words in their mouth. Don't score, rate or judge them or the piece. Plain and short.
