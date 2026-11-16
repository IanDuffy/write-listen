# Grading one live mid-chapter question against the guide's rules (voice/gpt_live/persona.py asides)

A separate Claude pass with a fresh context reads one scripted aside: the GPT-Live guide reads a
chapter, the listener taps and asks something, and the guide answers and goes back with
resume_chapter. Output: JSON per persona.GRADE_SCHEMA (same shape as prompts/live_grade.md). Inputs:
{{rules}} (the bodies of voice/gpt_live/instructions/40_reading.md, 50_asides.md and 80_policies.md),
{{scenario}} (what the scripted listener does and what should happen), {{context}} (the silent
context the app gave the guide at the tap: what was just read and what is coming), {{transcript}}
(timed lines, as in the onboarding grade).
---
You are reviewing a test of a voice guide in an audio learning app. The guide reads a chapter aloud; the listener taps to ask something; the guide answers and goes back to the reading by calling the backend tool resume_chapter, after leading back in its own words. The guide's words are a transcript of its speech. Grade the guide strictly against its own instructions:

<rules>
{{rules}}
</rules>

The scripted listener in this scenario, and what should happen:
<scenario>
{{scenario}}
</scenario>

The silent context the app gave the guide when the listener tapped:
<context>
{{context}}
</context>

The test (times in seconds; "tool" lines are backend tool calls the listener does not hear; "app" lines are what the app sent: passages to read, notes, mute and unmute):
<transcript>
{{transcript}}
</transcript>

Grade each of these rules, in this order, with these ids. Verdict "pass", "fail", or "not_tested". For every miss, quote the guide's exact words from the transcript (character for character, a short span is enough) and say in one sentence why it breaks the rule.

- reading: the passages were read word for word, with nothing before or after and no mention of passages or the app.
- stop: after the tap it stopped reading and listened; if the listener said nothing it opened in a word or two.
- answer: answered in one to three short sentences from the chapter's context or well-established knowledge; nothing invented; said so plainly when it didn't know.
- no_spoiler: if the answer is in what is coming, it said the chapter gets there and gave a taste at most (not_tested otherwise).
- wait_after_answer: never went back in the same turn as an answer, and didn't offer to go back.
- went_back: went back when it should (they said so, a closing signal, silence after the answer, "never mind"), not before; with a brief lead-back in its own words before resume_chapter; with rewind "paragraph" only when they were lost.
- unspoken: never said brackets, notes, tool names or its reasoning aloud.

Then list any other behaviour that looks wrong under other_issues, each with a quote. Score 0-10 as a listener would judge it by ear. Summary: three sentences at most.
