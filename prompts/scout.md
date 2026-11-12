# Prompt: scout (stage 1 of a seed run)

Inputs:
- {{seed}}: 00_source/seed.md, the topic in the listener's words and why it matters to them
- {{profile}}: listener/profile.md, what the listener has said about themselves (if any)
- {{searches}}, {{fetches}}: the scout's budget, config.yaml scout
- {{source_tiers}}: prompts/source_tiers.md
- {{target_minutes}}: the lesson's length, config.yaml (seed_runs)

Tools: web search and web fetch, with max_uses set to the budget.
Output: 01_map.md, the part inside <map>. It plays the role the episode map plays
for a transcript: the angles, choose and ledger stages read it.
---
A listener has asked for a lesson: about {{target_minutes}} minutes of narrated audio that a single narrator will read, researched and written for them. Here is what they said, in their own words:

<seed>
{{seed}}
</seed>

What they have told us about themselves before, if anything:

<profile>
{{profile}}
</profile>

Before anyone chooses an angle, scout the territory. Later, an editor will choose what the lesson argues, and research agents will dig into each idea with their own budgets. Your job is the map they work from: what is out there, what is remarkable, and where the good sources are.

You have {{searches}} searches and {{fetches}} page fetches; the tools refuse any more. Spend them on breadth. Look for:

- **The remarkable thing behind the ordinary.** What a listener would not expect: the history, science, person or argument sitting just behind the topic as they put it. A seed like "I pour concrete for a living" leads to Roman harbors that are still standing. Read the seed for what sits next to the listener's life, not only for the topic they named.
- **Stories and scenes.** People in places, in years, doing things. A lesson is heard once, at speaking pace; it carries on scenes.
- **Primary sources** that are freely available: texts, letters, papers, records, archives. And the authoritative secondary sources: encyclopedias of the field, university presses, peer-reviewed work.
- **Debates and surprises**: where experts disagree, what turned out to be wrong, what is still unknown.

Source tiers, for judging what you find:

{{source_tiers}}

Only cite a URL that a search returned or that you fetched. Never write a URL from memory, and do not state facts you did not find.

When you are done, write the map inside <map> tags, in markdown, in exactly these sections:

<map>
## Seed in brief
What the listener asked for, in a sentence or two of their own words, and what seems to lie behind it: why it might matter to them, from what they said. No guesses about personality or mood.

## Threads
Five to eight distinct threads a lesson could follow, each in two or three sentences: what it is, why a curious listener would care, and how it connects to the seed. Mark the three richest [RICH]. Mark any that rest on thin sources [THIN].

## People
The people who matter in this territory: who they were (one line, with dates), and the scene or story each brings, with its source URL.

## Works and primary sources
The texts, documents and records worth reading from, with author, date, a free URL where there is one, and what each could show.

## Stories, scenes and images
The concrete material you found: each a few sentences a writer could retell, with its source URL and tier.

## Claims worth checking
Factual claims central to the territory, one per bullet, with source URL and tier. Mark [CONTESTED] where sources disagree and [CHECK] where you found only one weak source.

## Candidate sources
The best sources you found, one per bullet: title, URL, tier, and what it is good for.

## Gaps
What you looked for and could not find, or found only in weak sources.
</map>
