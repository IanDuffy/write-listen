# Prompt: research plan (stage 4, step 1, Opus)

Inputs:
- {{angle}}: 03_angle.md
- {{map}}: 01_map.md
- {{min_topics}}, {{max_topics}}, {{searches}}, {{fetches}}: config.yaml

Output: 04_research/plan.md. Each '## Tn: Title' section is handed, as written,
to one research agent. Ian can edit the plan before research runs
(research --plan-only).
---
You are planning the research for a long-form audio chapter. Several research agents will work in parallel, one per topic you define. Each agent sees the angle, the map of the podcast episode, its own topic section from your plan, and the titles of the other topics. Nothing else. Each has about {{searches}} web searches and {{fetches}} page fetches.

The chapter's angle, with the commissioning editor's notes:

<angle>
{{angle}}
</angle>

A map of the podcast episode the chapter starts from:

<map>
{{map}}
</map>

Break the research into {{min_topics}} to {{max_topics}} topics. Good topics:

- Together, they cover everything the chapter will need: the primary texts it reads closely, the thinkers' lives and settings (each thinker enters the chapter with a scene, so the agent must find concrete places, years and incidents), the arguments and the strongest objections to them, the episode claims that need checking, and the modern scholarship that bears on the angle.
- Are separate enough that two agents will not spend their budgets on the same pages. Split by thinker or text where you can, not by abstract theme.
- Each fit one agent's budget. A topic that needs two whole books read closely is two topics.
- Put the primary texts first. The listener expects these texts to be over their head; the chapter will take them into the texts, so the agents must bring back exact passages with translator and reference.

Write the plan in markdown, exactly in this shape:

# Research plan: a working title for the chapter

## Overview
Three to five sentences: what the chapter needs from the research as a whole, and how the topics divide it.

## T1: a short topic title
- Goal: what this topic must establish for the chapter, in one or two sentences.
- Questions: the specific questions to answer, as a short list.
- Primary texts: which texts and passages to read, and where they are freely available online if you know (Project Gutenberg, Perseus, Wikisource, Internet Archive, the Stanford Encyclopedia of Philosophy for orientation).
- Secondary sources: the kind of sources to look for, and any you know to be authoritative.
- Episode claims to check: claims from the map marked [CHECK] or [CONTESTED] that fall in this topic, quoted.
- Scenes to find: the biographical or historical details the chapter needs to introduce a person or idea concretely.

## T2: ...

Number topics from T1 with no gaps. Write nothing after the last topic.
