# Prompt: draft one segment (stage 6)

Inputs:
- {{n}}, {{total}}: this segment's number and the segment count
- {{segment_brief}}: this segment's section of the brief
- {{entries}}: the dossier entries on this segment's 'Dossier:' line, in full
- {{target_words}}: this segment's target length
- {{first_or_later}}, {{last}}: position notes, filled in by the stage
---
Write segment {{n}} of {{total}}.

This segment's plan from the brief:

<segment_brief>
{{segment_brief}}
</segment_brief>

The dossier entries this segment must use:

<entries>
{{entries}}
</entries>

Length: about {{target_words}} words. Stay within 10% of that.

{{first_or_later}}
{{last}}

Before you finish, check: every factual sentence has its [Dn] marker; every direct quote comes from an entry marked verbatim: yes; no sentence repeats something an earlier segment already said; the segment ends on the hand-off the brief describes.
