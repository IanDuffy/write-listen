# Prompt: fix what the code checks found (Fast Start; stages fast_opening_edit and fast_lock)

Inputs:
- {{returned}}: what the editor returned: the opening's text, or a lock's JSON
- {{problems}}: what faststart/checks.py found in it, one problem per line

Sent once, as the last block of the same request the editor had, so the plan, the entries
and the drafts are all above it. What is still wrong afterwards is repaired by code where
that is safe (checks.repair) and recorded in decisions.md. A fix costs the listener about
as long as the edit did, so it changes only what the problems name.
---
An editor has done the job above. This is what came back:

<returned>
{{returned}}
</returned>

Code checks found these problems in it:

<problems>
{{problems}}
</problems>

Return it again with these problems fixed and nothing else changed. How to fix each kind:
- A quotation that no verbatim: yes entry backs: make it a paraphrase without quotation marks, keeping who said it ("Pliny argued that..."). If an entry marked verbatim: yes above has the words, you may quote that entry exactly instead. Never credit the words to someone else.
- A marker for an entry that is not in the dossier, or that the opening may not cite: cite the entry above that supports the sentence. If none does, cut the claim or soften it into interpretation.
- A bracketed tag, a stray bracket, an unclosed marker or a segment marker: remove it, or close the marker. [pause] on a line of its own is the only stage direction there is.
- A sentence or passage that stands in both sections, or twice in one: keep it in the one place where it does more, and take the other out.
- A passage declared moved that is not where it was declared: put it there, or correct "moved".
- An entry no longer cited, or text lost: if you removed the passage on purpose, list it in "cut", word for word as it stood in the draft it came from (the next section's draft included), with the reason. Put it back where it was only if it was dropped by accident. If the sentence is still there and you took the marker off because the entry does not support it, leave it as it is. The opening comes back as text alone, so for it there is no cut list: put back only what was dropped by accident, and add nothing after the text.

Return it in the form it came in, complete: the section's text alone, or the same JSON fields. No preamble and no notes.
