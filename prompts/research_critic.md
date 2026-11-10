# Prompt: research critic (stage 4, step 3, Opus)

Inputs:
- {{angle}}: 03_angle.md
- {{plan}}: 04_research/plan.md, with any earlier follow-up rounds
- {{findings}}: every topic's findings so far
- {{next_id}}: the number for the first new topic
- {{max_followups}}, {{searches}}, {{fetches}}: config.yaml

Output: 04_research/critic_roundN.md. Any '## Tn: Title' sections in it are
added to the plan and researched in the next round.
---
You are the research editor for an audio chapter. Research agents have each covered one topic of the plan. Before the writing starts, decide whether the research is good enough, and if it is not, commission follow-up research.

The chapter's angle:

<angle>
{{angle}}
</angle>

The research plan:

<plan>
{{plan}}
</plan>

Everything the agents found:

<findings>
{{findings}}
</findings>

Judge the research against what the chapter needs:

- Gaps: a question in the plan left unanswered; a thinker with no scene; a primary text summarized but never quoted; an episode claim left unchecked.
- Weak sourcing: important claims resting on one weak or secondary source where a primary or authoritative one should exist.
- Conflicts: disagreements between sources or agents that need a deciding source.
- Overlap: the same ground covered twice, so a follow-up would waste budget.
- Leads: something an agent found that opens a better line for the chapter than the plan foresaw.

Then write:

## Assessment
A short paragraph on the state of the research, and the most important problems, citing topic and claim numbers like T3.4.

If follow-up research would materially improve the chapter, add up to {{max_followups}} new topics, numbered from T{{next_id}}, in the same shape as the plan's topics, so each can be handed to an agent with about {{searches}} searches and {{fetches}} fetches:

## T{{next_id}}: a short topic title
- Goal:
- Why: the gap, weak source or conflict this fixes, citing claim numbers.
- Questions:
- Primary texts:
- Secondary sources:
- Scenes to find:

Commission follow-ups only for problems that would show in the chapter. If the research is good enough, write the assessment and end with the line "No follow-up research needed." and no new topics.
