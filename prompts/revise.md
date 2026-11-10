# Prompt: revise one segment (stage 9)

Inputs:
- {{n}}, {{total}}: segment number and count
- {{segment}}: the segment's draft text
- {{notes}}: the '## segNN' section of 08_edit_notes.md for this segment
---
Revise segment {{n}} of {{total}}.

The editor's notes for this segment:

<notes>
{{notes}}
</notes>

The segment as drafted:

<segment>
{{segment}}
</segment>
