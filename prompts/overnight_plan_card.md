# Prompt: the planning pass, step 3: the Up next card (overnight/planner.py)

Inputs:
- {{decided}}, {{topic}}, {{angle}}, {{why}}: step 1's choice
- {{words}}: what the listener said that this answers, verbatim (or "(none)")
- {{notes}}: the listener's notes with a request or queued topic (include or avoid), or "(none)"
- {{scout}}: step 2's notes
- {{profile}}: profile.md
- {{last}}: the last chapter's title and summary
- {{past}}: every past chapter's title (code rejects a near repeat)
- {{minutes}}: the chapter's length
- {{retry}}: empty, or overnight_plan_card_retry.md when the first title repeated a past one

Output: JSON (structured output; CARD_SCHEMA in overnight/planner.py), written into up_next.json. The summary
is a promise: the overnight outline starts from it and the promise check holds the chapter to it.
Model: stages.overnight_plan_card (Sonnet 5.5).
---
Write the Up next card for tonight's narrated audio chapter (about {{minutes}} minutes). The listener sees it in the evening; the chapter is researched and recorded overnight from it.

What it is about ({{decided}}):
- Topic: {{topic}}
- Angle: {{angle}}
- Why this, for them: {{why}}
- What they said that this answers: {{words}}
- Their notes: {{notes}}

The listener's profile:

<profile>
{{profile}}
</profile>

The last chapter they heard: {{last}}

A quick scout of what exists:

<scout>
{{scout}}
</scout>

Past chapters (never repeat or nearly repeat one of these titles or subjects):

{{past}}

Write:
- **title**: clear, like a book chapter or a podcast episode title. It says what the chapter is about. No clickbait: no teasing question the title withholds, no "you won't believe", no "the secret of", no colon-subtitle stack. Under ten words.
- **overview**: one line, under twenty words.
- **summary**: three or four sentences: the question the chapter takes on, the angle, and why it follows from what the listener has heard or said. Plain words, addressed to them ("you"). It is a promise the chapter must keep, so promise only what the scout suggests the research can deliver; no specific facts the scout did not see.
- **angle**: one or two sentences for the writer: the way in and what the chapter argues or shows.
- **research_questions**: three to five questions for tonight's research, each concrete and answerable from good sources, together covering what the summary promises.

{{retry}}
