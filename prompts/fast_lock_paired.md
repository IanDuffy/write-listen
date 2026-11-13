# Prompt: lock, with the next section's draft beside it (Fast Start; a fragment of fast_lock.md)

Inputs:
- {{n}}, {{next_n}}: the section being locked and the one after it
- {{next_draft}}: the latest draft of section n+1
- {{next_plan}}: section n+1's part of the outline
- {{next_target}}: section n+1's length

The look ahead is what the rolling edit is for (docs/fast-start.md, step 5): the editor may
move material between the two sections. Code checks that nothing was lost or duplicated
(faststart/checks.py move_problems, acceptance check 4), so a move must be declared in "moved"
and a removal in "cut".
---
The draft of section {{next_n}}, which plays next. It is beside you so that you can edit the two as a pair; it gets its own full edit when it is locked.

<draft n="{{next_n}}">
{{next_draft}}
</draft>

Section {{next_n}}'s plan from the outline (about {{next_target}} words):

<plan n="{{next_n}}">
{{next_plan}}
</plan>

Edit the pair:
- The join. Section {{n}} ends on its hand-off, and section {{next_n}} picks it up without recapping. If the next draft opens by retelling what section {{n}} has just said, cut the retelling there and list it in "cut".
- Material in the wrong section. If a passage in one draft does its work better in the other (a fact section {{n}} needs that sits in section {{next_n}}; a point section {{n}} makes that is section {{next_n}}'s job in the outline), move it. Lift the passage whole, with its markers, set it where it belongs, and take it out of the section it came from, so that it stands in exactly one place. Fit the sentences around it; leave no second version behind. Move only what earns it: each section keeps its own job, and neither should end far from its target length.
- Repetition between the two. Where both drafts make the same point, tell the same example or use the same quotation, keep it where it does more and cut it from the other.
- Otherwise leave the draft of section {{next_n}} alone. Do not polish it; that is its own edit's job.

In your reply, besides the fields described at the end:
- "next_text": the draft of section {{next_n}} as it now stands, in full, if you changed anything in it. An empty string if you changed nothing.
- "moved": one item for each passage moved between the two. "text": the passage exactly as it now stands where you put it. "from" and "to": the section numbers. An empty list if nothing moved.
- "cut", described at the end, covers both drafts: a passage you removed from the draft of section {{next_n}} is listed there too, word for word as it stood in that draft.
