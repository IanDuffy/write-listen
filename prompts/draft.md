# Prompt: draft one segment (stage 6)

Inputs:
- {{n}}, {{total}}: this segment's number and the segment count
- {{segment_brief}}: this segment's section of the brief
- {{claims}}: the dossier entries this segment must carry, in full
- {{target_words}}: this segment's target length
- {{first_or_later}}, {{last}}: position notes, filled in by the stage
---
Write segment {{n}} of {{total}}.

This segment's plan from the brief:

<segment_brief>
{{segment_brief}}
</segment_brief>

The dossier claims this segment must carry:

<claims>
{{claims}}
</claims>

Length: about {{target_words}} words. Stay within 10% of that.

{{first_or_later}}
{{last}}

Before you finish, check: every factual sentence has its [Cn] marker; no sentence repeats something an earlier segment already said; the segment ends on the hand-off the brief describes.
