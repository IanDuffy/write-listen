# Prompt: the Tester Demo's study report (`wl study report`, demo/report.py)

Inputs:
- {{count}}: how many testers there are (opened or not)
- {{stages}}: where each tester stopped, counted by code (not opened, intro, onboarding, chapter, debrief, done; "left early" marks those who went quiet before the end)
- {{material}}: per tester: label, name, the stage reached, the chapter's title, its parts with their approximate start minute, minutes heard of the chapter's length, the questions they asked mid-chapter (time and their words), the debrief summary (Claude's, from the end of the debrief), and the intro and debrief transcripts
- {{today}}: the date

Output: Markdown, written whole to study/report.md. Spec: docs/tester-demo.md, "Admin page and study report":
as changed by Ian's notes of 2026-11-16 (the debrief is a conversation around six topics, not five fixed
questions): per topic the common answers with counts and two or three quotes; moments testers named, matched to
chapter positions; drop-off by stage; bugs and confusions listed separately; three or four testers worth a
follow-up call, with the reason for each. It quotes and counts; no scores, no ranking.
Model: config.yaml models.writer (Opus 5.5).
---
Ian ran a small study: {{count}} testers each opened a private link to his audio app, talked with an interviewer, then with the app's guide, heard a short chapter made from that conversation (asking questions by voice along the way), and were debriefed by the interviewer. Write the study report he will read to decide what to change. Today is {{today}}.

The debrief was a conversation to gather product feedback, around these topics (not every tester was asked every one, and not in this order):
- What stood out, or what they'd tell Ian first.
- Whether this is something they would use, and when.
- Where they imagine it going: what they'd want it to become.
- The voice: how it was to listen to and talk with.
- Asking questions mid-chapter: how it went, or whether anything made them want to.
- Anything confusing, or that got in the way.

Where testers stopped, counted from their folders:
{{stages}}

<testers>
{{material}}
</testers>

Write the report in Markdown with exactly these sections, in this order:

# Tester Demo: study report

One short paragraph: how many testers opened the link, how many finished the debrief, and the two or three findings that matter most.

## By topic

One subsection per topic, in the order above (`### What stood out`, `### Would they use it, and when`, `### Where they imagine it going`, `### The voice`, `### Asking questions`, `### Confusing, or got in the way`). Under each: the common answers, each as a bullet with how many testers gave it ("5 of 9"), then two or three short quotes in the testers' own words, each attributed to the tester's label. Count only testers with whom the topic came up, and say how many that was. Give an answer that only one tester gave its own bullet only if it says something the others don't.

## Moments in the chapter

Specific moments testers named (something that held them, lost them or felt off) and the questions they asked mid-chapter. Match each to the chapter's position when the material allows it (the part's title and the minute, from the parts' start minutes and the times of their questions), and say when it doesn't. Group moments that recur across testers and count them.

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
