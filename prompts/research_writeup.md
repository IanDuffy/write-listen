# Prompt: research write-up (stage 04 and 04b)

Inputs:
- {{instructions}}: the agent's original prompt (research_row.md or research_verify.md, filled in)
- {{notes}}: any text the agent wrote before its tool loop paused
- {{material}}: every search result it saw and the full text of every page it fetched

Used when an agent's tool loop pauses after it has used any of its search or
fetch budget. Resuming the loop would give it the whole budget again (the API
applies max_uses per request), so instead this one call, with no tools, asks
for the findings from what the agent already read. Output: the same <research>
block.
---
You were researching one idea for an audio chapter, and your search and fetch budget is now spent: there will be no more searches or page reads. Write your findings from what you have already found. You have no tools now.

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

Cite only the URLs listed above. Mark a quote "verbatim: yes" only if its words are on one of the fetched pages above, copied character for character; anything else is a paraphrase, "verbatim: no", without quotation marks. Report what you could not establish in the Not found section.

Write the <research> block now, in the shape your instructions give.
