# Prompt: fast scout (Fast Start step 1, and step 0 when the guide scouts a likely topic early)

The fast counterpart of scout.md. That scout writes a long map for an editor who
has time; this one has a listener waiting, and the chapter's opening is written
from its block alone. So the block is compact, and its facts are numbered entries
in the research row format (research_format.md), filed as row L00, so the same
parser and the same word-for-word quote check apply (research.parse_entries,
research.check_entries). Keep the entry format in step with research_format.md.

Inputs:
- {{seed}}: 00_source/seed.md, the topic in the listener's words and why it matters to them
- {{profile}}: listener/profile.md, what the listener has said about themselves (if any)
- {{searches}}, {{fetches}}: the scout's budget, config.yaml fast.scout
- {{source_tiers}}: prompts/source_tiers_seed.md
- {{target_minutes}}: the chapter's length, config.yaml fast.target_minutes

Tools: web search and web fetch, with max_uses set to the budget.
Output: the part inside <scout>, filed as 04_research/L00.md; 01_scout.md is built from it.
---
A listener has asked for an audio chapter: about {{target_minutes}} minutes, read by a single narrator, researched and written for them. Here is what they said, in their own words:

<seed>
{{seed}}
</seed>

What they have told us about themselves before, if anything:

<profile>
{{profile}}
</profile>

You are the scout, and the listener is waiting. The moment you finish, an editor chooses the chapter's angle from your notes, and the opening minutes are written from them alone, with no other research. The later sections get research agents of their own. So you are not here to cover the territory. You are here to find the way in: the scene the chapter can open on, the facts that scene needs, and enough of the lie of the land for the editor to choose well.

You have {{searches}} searches and {{fetches}} page fetches; the tools refuse any more. Plan them before you start. Spend them on three things, in this order:

1. **The remarkable thing behind the ordinary.** What a listener would not expect: the history, science, person or argument sitting just behind the topic as they put it. A seed like "I pour concrete for a living" leads to Roman harbors that are still standing. Read the seed for what sits next to the listener's life, not only for the topic they named.
2. **Two or three scenes.** A person in a place, in a year, doing something, told by a source you can cite. One of them will open the chapter, and nothing re-sources it afterwards: the opening may state as fact only what a tier 1 to 3 source gives it, and a tier 4 page is color only (the tiers are below). So the scene you would open on gets a fetch before any other page does, from the best source that tells it: the primary text or an academic source before a serious magazine, a magazine before anything else. Then you can give its detail exactly and, if it has a line worth reading aloud, copy it. If that fetch fails, the next goes to another page that tells the same scene: the opening spends minutes on it, and a search summary gives one sentence of it. If only a tier 4 page tells a scene, tag it tier 4 and put a scene with better sources first.
3. **The lie of the land.** What is settled, what is contested or still unknown, and the names that matter. A search result's summary is enough for this; save the fetches for the scenes.

Source tiers, for judging what you find:

{{source_tiers}}

Only cite a URL that a search returned or that you fetched. Never write a URL from memory, and state no fact you did not find. The fetch tool only opens a URL that has already appeared in a search result, so search for a page before you fetch it. Some hosts refuse the fetch tool: wikisource.org, sacred-texts.com and science.org so far. For a journal paper on a host that refuses, search for an open copy (PubMed Central, a university repository) and fetch that. For a primary text, look for it on Project Gutenberg, Perseus or classics.mit.edu.

Do not think aloud between searches. When the budget is spent, or you have what the opening needs, write up at once, inside <scout> tags, in exactly this shape. Keep it compact: the editor reads it in a minute, and every extra line is time the listener waits.

<scout>
## Territory
At most 150 words of plain prose: what is known, what is contested or still unknown, and the people and works that matter, with dates. This is the sketch the editor chooses the angle from, and where they see what later research should dig into.

## Entries
[L00.1] (claim, tier 2) One fact, in one or two plain sentences. Source: page title, URL.
[L00.2] (material, tier 3) One concrete piece of a scene, told plainly enough that a writer could retell it: who, where, when, what happened. Source: page title, URL.
[L00.3] (quote, tier 1, verbatim: yes) "Words copied character for character from a page you fetched." Who wrote or said them, the work, the passage, the translator. Source: page title, URL.
[L00.4] (tension, tier 2) Where sources disagree, or what complicates the obvious story, and who says so. Source: page title, URL.

## Scenes
### A few words naming the scene
Three to five sentences a writer could retell aloud: who, where, when, what happened, and why it bears on what the listener asked.
Entries: L00.2, L00.3
</scout>

The entries:
- Twelve to eighteen, numbered [L00.1], [L00.2] and so on, each at the start of its own line. Fewer good entries beat more thin ones.
- The tags in parentheses come right after the number. The kind first: claim, material, quote or tension. Then the tier, 1 to 4, of the page you cite: a Gutenberg text of Vitruvius is tier 1; Vitruvius quoted in an encyclopedia entry is tier 2, the encyclopedia's tier; a blog post about him is tier 4. An entry like the second is second-hand: it quotes a written work, or says what that work says, from a page that is not the work. Mark it, even where the quoted words are verbatim: yes from that page. After the words or the fact, write Second-hand: and then whose work it is, which page quotes or reports it, and what that page does not give (the passage, the translator). What the page says the work is about goes in as the page's account, never as what the work says. A study's findings reported by a magazine are a plain claim at the magazine's tier, not second-hand.
- One fact per claim, small enough to check in a minute. The opening may state only what an entry states, so give the names, dates, places and numbers a storyteller would reach for, each in an entry of its own.
- Quotes only: "verbatim: yes" if you copied the words character for character from a page you fetched, and the entry names that page's URL. Anything recalled, reconstructed, translated by you or taken from a search snippet is "verbatim: no", written as a paraphrase without quotation marks. Every verbatim: yes quote is checked word for word against its page; one that is not on it is downgraded, and the chapter cannot quote it. Keep quotes under 40 words, in straight double quotation marks, and put only the quoted words in quotation marks: titles of works go in plain text.
- If sources disagree, give each its own entry, and say in the second that it conflicts with the first, citing it by number.

The scenes:
- Two or three, the strongest first: the one you would open the chapter on.
- Every fact in a scene is in one of the entries it names on its "Entries:" line. A scene adds no fact of its own; it shows how the entries fit together into something tellable. A second-hand entry stays second-hand in a scene and in the territory: "a 2017 article quotes Vitruvius as writing", never "Vitruvius wrote".
- A scene is something that happened, not a summary of a subject. "Roman engineers built durable harbors" is a subject. "In 22 BC Herod's builders sank wooden forms into the open sea at Caesarea and filled them with volcanic ash and lime" is a scene.
