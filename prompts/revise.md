# Prompt: revise one segment (stage 9)

Inputs:
- {{n}}, {{total}}: segment number and count
- {{segment}}: the segment's draft text
- {{notes}}: the '## segNN' sections of 08_edit_notes.md and 08b_factcheck.md for this segment, merged by code
---
Revise segment {{n}} of {{total}}.

The notes for this segment: the editor's, then the fact-checker's. Where they conflict, follow the fact-checker:

<notes>
{{notes}}
</notes>

The segment as drafted:

<segment>
{{segment}}
</segment>
