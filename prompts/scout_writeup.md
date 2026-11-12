# Prompt: scout write-up (stage 1 of a seed run)

Inputs:
- {{instructions}}: the scout's original prompt (prompts/scout.md, filled in)
- {{notes}}: any text the scout wrote before its tool loop paused
- {{material}}: every search result it saw and the full text of every page it fetched

Used when the scout's tool loop pauses after using any of its budget: resuming would
give it the whole budget again (max_uses applies per request), so this one call,
with no tools, asks for the map from what it already read. Output: the same <map> block.
---
You were scouting the territory for a listener's lesson, and your search and fetch budget is now spent: there will be no more searches or page reads. Write the map from what you have already found. You have no tools now.

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

Cite only the URLs listed above, and state only what the material supports. Put what you could not find in the Gaps section.

Write the <map> block now, in the shape your instructions give.
