# Prompt: memory pass after a conversation (Phase 1, milestone 5)

Inputs:
- {{profile}}: listener/profile.md as it stands (or a note that there is none)
- {{guide}}: listener/guide.md as it stands (or a note that there is none)
- {{history}}: listener/history.md, the chapters made so far, each with its run name
- {{known}}: what library.jsonl and seeds.jsonl already hold (recent items), so nothing is added twice
- {{transcript}}: the conversation just ended, from listener/sessions/<id>.jsonl
- {{today}}: the date of the conversation

Output: JSON (structured output; SCHEMA in stages/memory.py): the new profile.md
and guide.md, written whole, whether the listener asked to forget anything (code marks the
session so memory.recent never quotes it), and the run names of any chapters the listener asked
to forget (code removes them from history.md and seeds/), and the works, people, topics and future-chapter
seeds the listener mentioned (code appends them to library.jsonl and seeds.jsonl, overnight/library.py, and
keeps a seed's words only if the listener said them). Rules from docs/phase1-onboarding.md, "Memory store";
library and seeds from docs/overnight-chapters.md.
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

Already in their library and seeds (do not list these again):

<known>
{{known}}
</known>

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
- If this was a first meeting and the guide never said a name (it no longer introduces itself up front, so a short conversation can end before it does), return an empty string: the guide then has no name with them yet and is offered names to choose from next time. (The next conversation is still not a first meeting: they have talked.)
- If the guide said nothing about itself and the file exists, return it unchanged.

**forgot**: true if the listener asked the guide to forget anything in this conversation (something they said, a subject, a chapter), else false. Code then never quotes this conversation back to the guide.

**forget_chapters**: if the listener asked the guide to forget a chapter, or a subject that a chapter in the history is about, the run names of those chapters (the name in brackets after each title, before the date). Otherwise an empty list.

**library**: works (books, films, podcasts, articles, pieces of music), people and topics the listener mentioned in this conversation as something they read, watched, love or are curious about. Only what the listener brought up or took up with interest, not everything the guide named. `kind` is work, person or topic; `title` as they would say it; `creator` the author or maker if known (else empty); `note` a few words on what they said about it. Leave out anything already known (above) and anything they asked to forget. Often an empty list.

**seeds**: questions and ideas the listener raised that could become a future chapter: something they wondered about, asked and did not get to the bottom of, or said they'd like to hear more about. `kind` is question or idea; `text` the subject as a plain line; `words` their exact words from the transcript (copied, not paraphrased), or empty. Leave out the topic of a chapter started in this conversation (the start_chapter tool call), anything already known (above) and anything they asked to forget. Often an empty list.

profile and guide are plain markdown, no headings beyond one title line, written to be read by the guide at the start of the next conversation.
