# Prompt: lock, on its own (Fast Start; a fragment of fast_lock.md)

Inputs:
- {{n}}: the section being locked

The max_hold_seconds fallback (docs/fast-start.md, The threshold): the listener has waited
long enough, so the section is locked without the next section's draft beside it. It is
still a full edit; it only loses the look ahead.
---
You are locking section {{n}} on its own: the listener has reached the end of the audio and is waiting for it, and the next section's draft is not ready to read beside it. This is still the full edit. Take particular care that the section ends on its hand-off as a place to rest (rule 9): the section after it may be a while.

In your reply, besides the fields described at the end, "next_text" is an empty string and "moved" an empty list: there is no second draft.
