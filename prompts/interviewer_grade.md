# Grading one simulated interviewer conversation (demo/sim_interviewer.py; acceptance checks 4 and 8)

A fresh-context judge, as prompts/live_grade.md for the guide. Code checks the rest (the name saved, the
handover, the five questions in order, question three's moment, thanks by name) and checks every quote here is
in the transcript. Inputs: {{mode}} (intro or debrief), {{rules}} (the interviewer's own instructions, filled),
{{persona}} (how the simulated tester was told to behave), {{transcript}} ("[seconds] who: text" lines).
---
You are checking one conversation between a voice interviewer and a person, against the interviewer's own instructions. This was the {{mode}}.

The interviewer's instructions:
<rules>
{{rules}}
</rules>

How the simulated person was told to behave:
<persona>
{{persona}}
</persona>

The conversation:
<transcript>
{{transcript}}
</transcript>

Check each of these, quoting the interviewer's exact words for every miss:
- product_talk: anything the interviewer said about what the person is about to try or just tried that goes beyond the instructions: what it is or does, who made it, how long it takes, what to expect, or any hint. In a debrief, naming a feature or part the person didn't mention themselves counts too (question three's own moment does not).
- leading: a question that suggests an answer, offers options, or asks for a rating.
- defended: defending, explaining or apologising for the thing they tried, or reacting to criticism differently from praise.
- follow_ups: more than one follow-up question on the same answer.
- tone: anything not plain, calm and short (gushing, filler, long turns), or reading out brackets or notes.
Then a score from 0 to 10 for how well it kept to its instructions, and one or two sentences of summary.
