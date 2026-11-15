# Prompt: the intro brief for the morning intro (overnight/intro.py)

Inputs:
- {{title}}, {{summary}}: the Up next card's
- {{turns}}: what each section of the outline lands, the through-line and closing idea (05_outline.json): what
  the findings must not give away
- {{dossier}}: 04_dossier.md, the research the chapter was written from
- {{source}}: where the chapter came from (decided, kind, ref)
- {{words}}: the listener's words it answers (card source.words, request topic and notes), one per line

Output: JSON (structured output; SCHEMA in overnight/intro.py). Code drops findings without a real dossier entry
or sharing a twelve-word run with the script, and renders intro_brief.md under fixed headings.
Model: stages.overnight_intro (Sonnet 5.5). It never sees the script.
---
In the morning a guide will greet a listener live and introduce tonight's narrated audio chapter in under a minute, before the recording plays. The guide gets only what you write here, so it cannot give away what is coming.

The chapter: "{{title}}"

Its promise (the listener has read this):

{{summary}}

What the chapter's sections land, its main turns. The listener should meet these in the chapter, not in the intro:

<turns>
{{turns}}
</turns>

The research the chapter was written from:

<dossier>
{{dossier}}
</dossier>

Where the chapter came from: {{source}}
What the listener said that it answers:
{{words}}

Write:
- **findings**: two or three things from the research the guide may mention as what surprised it. Each is surprising and concrete, true to its dossier entry, and safe: not one of the main turns above, not the chapter's ending or its answer to its own question, not a quote the chapter is built around. A side detail that makes someone want to listen. One or two sentences each, in plain spoken words. Give each its dossier entry (D12).
- **why_for_them**: two or three sentences for the guide: why this chapter follows from this listener: which question, seed or conversation it came from, quoting their words above exactly where there are any. If there are none, say plainly what of theirs it builds on. Nothing about what the chapter reveals.
