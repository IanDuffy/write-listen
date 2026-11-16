# Prompt: the Tester Demo's study report (`wl study report`, demo/report.py)

Inputs:
- {{count}}: how many testers there are (opened or not)
- {{stages}}: where each tester stopped, counted by code (not opened, intro, onboarding, chapter, debrief, done; "left early" marks those who went quiet before the end)
- {{material}}: per tester: label, name, the stage reached, the chapter's title, its parts with their approximate start minute, minutes heard of the chapter's length, the questions they asked mid-chapter (time and their words), the debrief summary (Claude's, from the end of the debrief), and the intro and debrief transcripts
- {{today}}: the date

Output: Markdown, written whole to study/report.md. Spec: docs/tester-demo.md, "Admin page and study report":
per core question the common answers with counts and two or three quotes; where people were most into it and
where they drifted, matched to chapter positions; drop-off by stage; bugs and confusions listed separately;
three or four testers worth a follow-up call, with the reason for each. It quotes and counts; no scores, no ranking.
Model: config.yaml models.writer (Opus 5.5).
---
Ian ran a small study: {{count}} testers each opened a private link to his audio app, talked with an interviewer, then with the app's guide, heard a short chapter made from that conversation (asking questions by voice along the way), and were debriefed by the interviewer. Write the study report he will read to decide what to change. Today is {{today}}.

The debrief asked the same five core questions, in this order:
1. Take me through what that was like, from the start.
2. Was there a moment you were most into it? And a moment you drifted, or something felt off?
3. At [time] you asked [their words]. How did that go? (Or, if they asked nothing: did anything make you want to stop it and ask something?)
4. How would you describe it to a friend?
5. If another one were waiting for you tomorrow morning, would you listen? What would make the difference?

Where testers stopped, counted from their folders:
{{stages}}

<testers>
{{material}}
</testers>

Write the report in Markdown with exactly these sections, in this order:

# Tester Demo: study report

One short paragraph: how many testers opened the link, how many finished the debrief, and the two or three findings that matter most.

## The five questions

One subsection per core question (`### 1. What it was like`, and so on). Under each: the common answers, each as a bullet with how many testers gave it ("5 of 9"), then two or three short quotes in the testers' own words, each attributed to the tester's label. Count only testers who answered that question. Give an answer that only one tester gave its own bullet only if it says something the others don't.

## Most into it, and drifting

Where people were most absorbed and where they drifted or something felt off. Match each moment to the chapter's position when the material allows it (the part's title and the minute, from the parts' start minutes and the times of their questions), and say when it doesn't. Group moments that recur across testers and count them.

## Drop-off

Where testers stopped, from the counts above: a short table (stage, count, labels). Then a sentence on any pattern in why, only from what the material shows.

## Bugs and confusions

Every bug, glitch or moment of confusion a tester mentioned or the transcripts show (a mishearing, a long silence, the guide repeating itself, not knowing what to do), one bullet each, with the tester's label and their words where they said it. Keep this separate from opinions about the product.

## Worth a follow-up call

Three or four testers Ian should call, each with one or two sentences on why: something they said that is worth hearing more of, a strong reaction either way, a confusion only they hit. Name them by label.

Rules:
- Quote and count. Do not score, rate or rank testers or the product, and do not add recommendations of your own beyond the follow-up list.
- Quotes are the testers' exact words from the transcripts or the summaries' quotes, short (under 25 words), in quotation marks. Never put words in a tester's mouth; paraphrase without quotation marks when you must.
- Only what the material shows. If a section has nothing (no one reached the debrief, no bugs mentioned), say so in one line.
- The interviewer's and the guide's words are context, not findings.
- Plain, direct sentences; no padding, no preamble about the method.
