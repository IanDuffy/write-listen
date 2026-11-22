# One look by the debrief's earpiece (demo/debrief_earpiece.look)

Inputs: {{elapsed}}, {{trigger}} (debrief_earpiece.TRIGGER_TEXT), {{hearing}} (their words in progress), {{questions}}
(the interviewer's lines with a question so far, its opening included), {{time_up}}, {{did}} (the account of what they
did, demo/interviewer.account), {{visit}} (demo/debrief.visit_brief), {{plan}}, {{last_read}}, {{notes}} (its notes so
far), {{transcript}} (the debrief so far).
---
<state>
Elapsed in the interview: {{elapsed}}. You are looking because: {{trigger}}.
Their words in progress (not yet a finished line): {{hearing}}
Questions the interviewer has asked so far, its opening included: {{questions}}.
The app has told it it is at time: {{time_up}}.
</state>

<what_they_did>
{{did}}
</what_they_did>

<their_visit>
{{visit}}
</their_visit>

<your_plan>
{{plan}}
</your_plan>

<your_last_read>
{{last_read}}
</your_last_read>

<your_notes_so_far>
{{notes}}
</your_notes_so_far>

<interview>
{{transcript}}
</interview>

Update your plan, then decide whether to send a note.
