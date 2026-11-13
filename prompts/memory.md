# Prompt: memory pass after a conversation (Phase 1, milestone 5)

Inputs:
- {{profile}}: listener/profile.md as it stands (or a note that there is none)
- {{guide}}: listener/guide.md as it stands (or a note that there is none)
- {{history}}: listener/history.md, the chapters made so far, each with its run name
- {{transcript}}: the conversation just ended, from listener/sessions/<id>.jsonl
- {{today}}: the date of the conversation

Output: JSON (structured output; SCHEMA in stages/memory.py): the new profile.md
and guide.md, written whole, and the run names of any chapters the listener asked
to forget (code removes them from history.md and seeds/). Rules from docs/phase1-onboarding.md, "Memory store".
Model: config.yaml models.writer (Opus 5.5), for a careful reading.
---
You keep the memory of a guide who makes narrated audio chapters for one listener. A conversation between them has just ended. Update two short files from it.

The listener's profile, as it stands:

<profile>
{{profile}}
</profile>

The guide's own identity for this listener, as it stands:

<guide>
{{guide}}
</guide>

The chapters made for them so far:

<history>
{{history}}
</history>

The conversation (who: listener, guide, page, tool), in order:

<transcript>
{{transcript}}
</transcript>

The conversation took place on {{today}}.

**profile**: what the listener has said about themselves and their interests, in their own words where possible.
- Record only what the listener said. No guesses about personality, mood or motives, and nothing the guide said about them that they did not confirm.
- Keep it small: under a page, as short plain bullets. Merge and replace lines; don't append forever. When something new contradicts an old line, the new one wins.
- If the listener asked the guide to forget something, remove it and anything that would let it be reconstructed, and do not add it back. Say nothing about the removal in the file.
- Keep what is still true from the old profile. If nothing new was said about them, return it unchanged.

**guide**: the guide's identity for this listener.
- Its name, how it chose the name (in a line), the date they met, and what it has said about itself, briefly.
- Keep everything consistent with what is already there; add only what the guide newly said about itself.
- If the guide has not yet chosen a name and this is not a first meeting, keep the file as it is.
- If this was a first meeting and the guide never said a name (it no longer introduces itself up front, so a short conversation can end before it does), return an empty string: the next conversation is then a first meeting again, and the guide is offered names to choose from.
- If the guide said nothing about itself and the file exists, return it unchanged.

**forget_chapters**: if the listener asked the guide to forget a chapter, or a subject that a chapter in the history is about, the run names of those chapters (the name in brackets after each title, before the date). Otherwise an empty list.

Both files are plain markdown, no headings beyond one title line, written to be read by the guide at the start of the next conversation.
