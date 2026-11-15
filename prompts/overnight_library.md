# Prompt: which of the listener's questions a finished chapter left open (overnight/library.py)

Inputs:
- {{title}}: the chapter's title
- {{script}}: the chapter as spoken (09_script_final.md, markers stripped)
- {{questions}}: numbered questions the listener asked during asides, in their words, with the section they asked in

Output: JSON (structured output; LIBRARY_SCHEMA in overnight/library.py): {"open": [{"n", "question"}]}.
Code keeps the listener's own words beside each restatement (seeds.jsonl, kind question).
Model: stages.overnight_library (Sonnet 5.5, effort low).
---
A listener heard an audio chapter called "{{title}}". While it played they stopped it a few times to ask the guide something. Their questions, in their words:

<questions>
{{questions}}
</questions>

The chapter as it was spoken:

<script>
{{script}}
</script>

Pick the questions the chapter itself did not answer: not later in the chapter, not anywhere in it. The guide answered each briefly at the time; what matters here is whether a future chapter could take the question further. Leave out anything that is not really a question about the subject (asking the guide to repeat something, to go on, to go back, small talk about the app).

For each one you keep, give its number and restate it in one plain sentence as a question a later chapter could take on. Keep the listener's meaning; do not widen it into a different subject.

If the chapter answered them all, return an empty list.
