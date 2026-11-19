# Grading one simulated interviewer conversation (demo/sim_interviewer.py; acceptance checks 4 and 8)

A fresh-context judge, as prompts/live_grade.md for the guide. Code checks the rest (the name saved, the
handover, the intro's length and its one explaining turn, the debrief's number of questions, their own moment
named, nothing asked that the record rules out, direct questions answered, no question after they said they
were done, not ended before they had said anything, the farewell by name) and checks every quote here is in the transcript. `not_happened` since Ian's notes of 2026-11-18 (his
debrief said "Earlier, you chose not to cut in" when he had heard none of the piece). Inputs: {{mode}} (intro or debrief), {{rules}} (the interviewer's own instructions, filled),
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
- product_talk: anything the interviewer said about what the person is about to try or just tried that goes beyond the instructions: what it is or does, what it will be about, or any hint. Saying Ian built it, and how the session will go, is allowed. In a debrief, anything on the record of what they did is allowed.
- not_happened: in a debrief, a question or remark about something the record of what they did doesn't list or rules out (how the piece was when they heard none of it, a question they never asked, "you chose not to"), or about whether they would want something that already exists.
- leading: a question that suggests an answer, offers options, or asks for a rating.
- defended: defending, explaining or apologising for the thing they tried, or reacting to criticism differently from praise.
- follow_ups: interrogating: question after question with no response to what the person just said, a follow-up to an answer that was already clear, more questions than the instructions allow, going on to another subject after the person had given enough, sounded finished or asked to stop, ending after the opening question before the person had said anything specific when they hadn't asked to stop, or a direct question from the person left unanswered.
- tone: anything not warm, plain and short (gushing, filler, speeches beyond the one explaining turn the intro asks for), an intro that stops for an answer after every sentence, going silent when it was its turn, or reading out brackets or notes.
Then a score from 0 to 10 for how well it kept to its instructions, and one or two sentences of summary.
