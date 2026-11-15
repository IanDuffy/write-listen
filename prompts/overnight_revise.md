# Prompt: revise one section so the chapter keeps its promise (Overnight Chapters, stage overnight_revise)

Inputs:
- {{n}}, {{total}}: the section being revised, the section count
- {{summary}}: the Up next card's summary, the promise
- {{drift}}: how the chapter drifted from it, one line each (overnight_promise.md)
- {{fix}}: the judge's fix note for this section
- {{others}}: every other section's text, as <section n="N"> blocks, for context (not changed here)
- {{section}}: this section's text, with its [Dn] markers
- {{plan}}: this section's part of the outline
- {{entries}}: the whole dossier

System prompt: overnight_context.md (the rules, the style guide, the outline). One revision only: no
check follows except code (faststart/checks.py: markers, quotes; a reply that lost the section is
dropped). The first line must stay "Revise section N of M" (tests read it).
---
Revise section {{n}} of {{total}}. The chapter is written and edited. A check against the card the listener read the evening before found that it drifted from what the card promised. This section is one of those that should change to keep the promise.

What the listener was promised:

<summary>
{{summary}}
</summary>

How the chapter drifted:

<drift>
{{drift}}
</drift>

What this section should do differently:

<fix>
{{fix}}
</fix>

The other sections, as they stand. They will not change, so do not repeat what they tell, and keep the joins: this section still picks up where the one before it ends and hands off to the one after it.

<others>
{{others}}
</others>

Section {{n}}'s plan from the outline:

<plan>
{{plan}}
</plan>

The dossier entries, the only source of facts:

<entries>
{{entries}}
</entries>

Section {{n}} as it stands:

<section n="{{n}}">
{{section}}
</section>

Make the change the fix asks for, and only that: where the section is right, keep its words. Every rule in the system prompt still holds: each factual sentence keeps or gains its [Dn] marker from the entries above, a direct quote only from an entry marked verbatim: yes, nothing an earlier section told retold, nothing about how the chapter was made. Keep it within about 15% of its present length.

Output only the section's text, in full: plain paragraphs with their markers. No segment marker, no title, no notes.
