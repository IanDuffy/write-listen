# Grading one simulated interviewer conversation (demo/sim_interviewer.py; acceptance checks 4 and 8)

A fresh-context judge, as prompts/live_grade.md for the guide. Code checks the rest (the name saved, the
handover, the topics that came up, their own moment named, direct questions answered, the closing question,
thanks by name) and checks every quote here is in the transcript. Inputs: {{mode}} (intro or debrief), {{rules}} (the interviewer's own instructions, filled),
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
- product_talk: anything the interviewer said about what the person is about to try or just tried that goes beyond the instructions: what it is or does, what it will be about, or any hint. Saying Ian built it, and how the session will go, is allowed. In a debrief, bringing up a part or moment the person didn't mention themselves counts, except the voice, asking questions and their own listed moments.
- leading: a question that suggests an answer, offers options, or asks for a rating.
- defended: defending, explaining or apologising for the thing they tried, or reacting to criticism differently from praise.
- follow_ups: interrogating: question after question with no response to what the person just said, or a direct question from the person left unanswered.
- tone: anything not warm, plain and short (gushing, filler, long speeches), rushing through the intro, going silent when it was its turn, or reading out brackets or notes.
Then a score from 0 to 10 for how well it kept to its instructions, and one or two sentences of summary.
