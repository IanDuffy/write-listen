# Prompt: fast scout write-up (Fast Start step 1)

The fast counterpart of scout_writeup.md, which cannot be reused: it asks for the
<map> block and its Gaps section. Used when the fast scout's tool loop pauses after
using any of its budget: resuming would give it the whole budget again (max_uses
applies per request), so this one call, with no tools, asks for the <scout> block
from what it already read. Output: the same <scout> block.

Inputs:
- {{instructions}}: the scout's original prompt (prompts/fast_scout.md, filled in)
- {{notes}}: any text the scout wrote before its tool loop paused
- {{material}}: every search result it saw and the full text of every page it fetched
---
You were scouting for a listener's audio chapter, and your search and fetch budget is now spent: there will be no more searches or page reads. The listener is waiting. Write up what you have already found. You have no tools now.

Your original instructions:

<instructions>
{{instructions}}
</instructions>

Your notes so far:

<notes>
{{notes}}
</notes>

Everything your searches and fetches returned:

<material>
{{material}}
</material>

Cite only the URLs listed above, and state only what the material supports. Mark a quote "verbatim: yes" only if its words are on one of the fetched pages above, copied character for character; anything else is a paraphrase, "verbatim: no", without quotation marks. If the material holds fewer than twelve sound entries, write fewer: never fill the count with what you did not find.

Write the <scout> block now, in the shape your instructions give.
