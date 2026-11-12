# Prompt: angles, seed run (stage 2)

The seed-run variant of angles.md: the lesson starts from a listener's seed and
the scout's map, not an episode.

Inputs:
- {{seed}}: 00_source/seed.md, the topic in the listener's words and why
- {{profile}}: listener/profile.md (if any)
- {{map}}: 01_map.md, the scout's map of the territory and its sources
- {{target_words}}, {{target_minutes}}: config.yaml (seed_runs)

Output: 02_angles.md. The choose stage picks one.
---
We are making one narrated audio lesson of about {{target_words}} words (about {{target_minutes}} minutes) for one listener, from a topic they asked about. They are curious and not a specialist. The lesson should give them something they did not expect: the remarkable thing behind the topic as they put it, told through scenes and the sources themselves.

What the listener said:

<seed>
{{seed}}
</seed>

What they have told us about themselves before, if anything:

<profile>
{{profile}}
</profile>

A scout's map of the territory, with the sources it found:

<map>
{{map}}
</map>

Propose three or four candidate angles for the lesson. Make them genuinely different from one another: different theses, different threads, not one idea in four costumes. Each must answer to what the listener asked, and to why they asked it.

For each angle write:

### Angle N: a working title
- **Thesis:** one sentence the lesson will argue. A thesis is a claim someone could disagree with, not a topic.
- **Why this listener:** why it fits what they said and why they said it, and what they would learn that they could not have guessed.
- **Threads it develops:** which threads from the map, and how. Prefer threads marked [RICH]; say if one is [THIN].
- **Shape:** the arc in three sentences, from the opening scene to the closing turn. The lesson is short: {{target_minutes}} minutes holds three or four ideas, not more.
- **Research it needs:** the primary sources to read from and the facts to establish, and which the map already found.
- **Risk:** what could make this angle fall flat or go wrong, including thin sources.

Then add a short section, **My pick**, naming the angle you would choose and why, in three sentences or fewer.
