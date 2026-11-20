# The director's moment: the user message for each decision (voice/gpt_live/director.py `moment`)

Inputs: {{phase}} (a first conversation with no chapter yet, or the chapter being made), {{seconds}} into the
conversation (page/live.js), {{explained}} (the guide has said what it does: the page's or the transcript's
"audio chapter"), {{started}} (start_chapter returned a chapter), {{turns}} (the listener's turns, fragments heard
within a moment of each other merged), {{silence_secs}} and {{last_who}} (the silence right now and whose line
came last), {{profile}} (what is known about the listener: guide.Session.values identity and profile) and
{{transcript}} (this conversation, with the app's notes to the guide marked "app note"). Ian, 2026-11-19: see
prompts/live_director.md. {{explained}} is also yes for a returning listener, who already knows (their guide has
offer_known.md, never says "audio chapters", and must never offer to explain again; the review of 2026-11-19).
---
<state>
Phase: {{phase}}.
Seconds into the conversation: {{seconds}}.
The guide has said what it does: {{explained}}. (Yes too for a returning listener, who already knows.)
A chapter is under way: {{started}}.
Listener turns so far: {{turns}}.
Right now: nobody is speaking; {{silence_secs}} seconds since the {{last_who}}'s last line.
</state>

<profile>
{{profile}}
</profile>

<transcript>
{{transcript}}
</transcript>

Decide the guide's next move.
