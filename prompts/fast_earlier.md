# Prompt: the chapter so far (Fast Start; a fragment)

No inputs. The first block of every draft and lock request, followed by one block per earlier
section (`<section n="2" state="locked">`), built by faststart/sections.py. The fast
counterpart of draft_earlier.md. It never changes, and locked sections never change, so these
blocks are a cache prefix that later calls of the same kind read: keep this text free of
anything that varies from call to call.
---
The chapter so far, one section per block, in order. A section marked locked has been edited and narrated: the listener has heard it, or is hearing it now, and it cannot change. A section marked draft is written but not yet edited: its wording may still change, and the listener has not heard it.
