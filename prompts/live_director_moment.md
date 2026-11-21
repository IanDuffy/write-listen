# The earpiece's look: the user message for each look (voice/gpt_live/director.py `moment`)

Inputs: {{elapsed}} (m:ss into the conversation), {{trigger}} (why the page asks, in plain words: the regular look
with something new, a listener turn just ended, the guide just called start_chapter, the guide just offered to say
what it does, or said it, a silence of N seconds), {{speaking}} (from the page's input and output audio: "the
listener is speaking", "the guide is speaking" or "nobody is speaking"; a line reaches the transcript only when it
ends), {{last_who}} and {{silence_secs}} (whose line ended last, "listener" or "guide", and how many seconds ago),
{{explained}} (yes once a guide line said "audio chapter", which a cut-off line also does; yes too for a returning
listener), {{started}} (yes once start_chapter returned a chapter), {{profile}} (guide.Session.values identity and
profile), {{plan}} (its plan from its last look, one numbered line per item with its state, or a first-look line),
{{last_read}} (its read from its last look), {{notes}} (its whispers so far with times and kinds, and the app's
clear at the start), {{transcript}} (guide, listener, app notes and tools, timestamped m:ss) and {{longest}} (the
listener's longest line so far, in words; director.look). Every one must be supplied. Ian, 2026-11-19, and the blind
judge of the v3 replay: see prompts/live_director.md.
---
<state>
Elapsed: {{elapsed}}. You are looking because: {{trigger}}.
Right now: {{speaking}}. The last line that ended was the {{last_who}}'s, {{silence_secs}} seconds ago.
A guide line has said what it does: {{explained}}. (Yes too for a returning listener, who already knows. Check
the transcript that the explanation was said whole, not cut off.)
A chapter is under way: {{started}}.
The listener's longest line so far: {{longest}} words.
</state>

<profile>
{{profile}}
</profile>

<your_plan>
{{plan}}
</your_plan>

<your_last_read>
{{last_read}}
</your_last_read>

<your_whispers_so_far>
{{notes}}
</your_whispers_so_far>

<transcript>
{{transcript}}
</transcript>

Update your plan, then decide whether to whisper.
