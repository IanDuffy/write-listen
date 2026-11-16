# Prompt: the chapter card shown while a fast chapter is made (faststart/card.py)

Modelled on overnight_plan_card.md. One call at start_chapter, beside the run: the outline's title arrives
about three and a half minutes later, and the listener should see what the chapter is about while they are
still talking with the guide (Tester Demo, step 4). Nothing has been researched yet, so the card promises only
what the topic itself does.

Inputs:
- {{seed}}: 00_source/seed.md: the topic as the guide framed it, what the listener said, why, include or avoid
- {{topic}}: the seed's topic line (the guide's framing from start_chapter)
- {{minutes}}: the chapter's length (the run's fast.target_minutes)

Output: JSON (structured output; card.SCHEMA): title, overview, summary. Written to <run>/fast/card.json.
Model: stages.fast_card (Sonnet 5.5).
---
Write the card for a narrated audio chapter of about {{minutes}} minutes that is being made right now for one listener, from a topic they just settled on in conversation. They see the card on screen while it is written; the chapter starts playing a few minutes later.

The topic: {{topic}}

Everything they said about it:

<seed>
{{seed}}
</seed>

Write:
- **title**: clear, like a book chapter or a podcast episode title. It says what the chapter is about. No clickbait: no teasing question the title withholds, no "you won't believe", no "the secret of", no colon-subtitle stack. Under ten words.
- **overview**: one line, under twenty words.
- **summary**: three or four sentences, addressed to them ("you"): the question the chapter takes on and why it follows from what they said, in plain words. Nothing has been researched yet, so name no specific fact, figure, name or date that they did not give; promise the question, not the answers.
