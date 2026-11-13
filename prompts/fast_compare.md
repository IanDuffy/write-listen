# Prompt: compare two scripts blind (Fast Start, stage fast_compare; faststart/compare.py)

Acceptance check 11 of docs/fast-start.md: one seed is made twice, once section by section
while the listener listens and once the slower way, and a separate model pass with fresh
context compares the two final scripts on arc, repetition and accuracy against the dossier.

The judge is never told how either script was made, nor that they were made differently. Code
decides which script is A and which is B (by a hash of the two run names) and unblinds the
answer afterwards. Each script is judged against its own dossier: the two were researched
separately, so a fact in one dossier is no evidence for the other script.

Structured output (compare.SCHEMA): criteria (arc, repetition, accuracy: verdict, reason,
quotes), overall (verdict, reason), differences (script, quote, why_it_matters). Code checks
that each quotation is in the script it names.

Inputs:
- {{script_a}}, {{script_b}}: the two final scripts, with their section markers and [Dn] markers
- {{dossier_a}}, {{dossier_b}}: each script's own dossier entries
---
Two scripts for the same narrated audio chapter are below, A and B. They were written separately from the same request by the same listener, and each was researched separately. One listener will hear one of them, once, at walking pace, with no way to look back. Compare them on three things and say which serves that listener better.

A `<!-- segNN -->` line starts a section. A marker like [D12] after a sentence names the dossier entry that sentence rests on; the listener never hears the markers. A line that says only [pause] is a held beat.

Script A:

<script_a>
{{script_a}}
</script_a>

The dossier script A was written from. Each entry has its number, its kind, its source tier (1 is a primary source) and whether it is a verbatim quotation:

<dossier_a>
{{dossier_a}}
</dossier_a>

Script B:

<script_b>
{{script_b}}
</script_b>

The dossier script B was written from:

<dossier_b>
{{dossier_b}}
</dossier_b>

Judge each script against its own dossier only. The two dossiers were gathered separately: an entry in one says nothing about whether the other script is right.

The three criteria:

1. **arc**. Does the chapter go somewhere? Look at whether the opening sets a question the listener wants answered, whether each section moves that question on and hands off to the next, whether the order is the order of an argument or a story and not a list, and whether the ending lands what the opening began. A chapter whose sections could be shuffled without loss has no arc.
2. **repetition**. Does the chapter say the same thing twice? Count a fact, an example, a scene or a point that a later section tells again as if for the first time, and a section that opens by retelling the one before it. A deliberate callback, a few words that pick up an earlier image to turn it, is not repetition.
3. **accuracy**. Does every factual sentence say what its dossier entry says? Look for a claim stronger than its entry, a number, date or name that differs from it, a sentence in quotation marks that is not word for word in an entry marked verbatim: yes, a marker that names an entry which is not in the dossier, and a factual sentence with no marker at all. Do not use what you know from elsewhere: the question is whether the script is faithful to its own research.

For each criterion give:

- **name**: arc, repetition or accuracy.
- **verdict**: "A", "B" or "tie". Say tie only when you could not defend either choice; a small real difference is still a difference.
- **reason**: two or three sentences that name what decided it.
- **quotes**: the passages that show it, up to three, each with **script** ("A" or "B"), **text** (copied word for word from that script, one or two sentences, without its markers) and **why** (one sentence).

Then:

- **overall**: the **verdict** ("A", "B" or "tie") and the **reason**, in two or three sentences: which script should the listener hear, and what tips it. Accuracy failures that would mislead the listener outweigh polish.
- **differences**: the three to six differences between the scripts that matter most to a listener, most important first. Each has **script** (the one the quotation comes from), **quote** (word for word from that script, without markers) and **why_it_matters** (one sentence, saying what the other script does instead where that is the point).

Quote exactly. A quotation that is not in the script it names is discarded.
