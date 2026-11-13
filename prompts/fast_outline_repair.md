# Prompt: fast outline repair (Fast Start step 2, only when the edited outline fails the code check)

One call, told exactly which rules the outline broke (outline.validate). It mends
those and nothing else: the arc has already had its edit. It is not made for word
targets alone: code scales those into range first (outline.repair_targets), so this
call runs only when some other rule is broken. If its result still fails, code
scales the word targets again; if that is not enough, the first outline stands
unedited when it is sound, and otherwise the run stops.

Inputs:
- {{outline}}: the edited outline, as JSON
- {{problems}}: the rules it broke, one per line
- {{scout}}, {{scout_numbers}}: 01_scout.md and its entry numbers
- the sizes and research budget of fast_outline.md: {{min_sections}}, {{max_sections}}, {{target_words}},
  {{min_words}}, {{max_words}}, {{opening_min_words}}, {{opening_min_minutes}}, {{section_min_words}},
  {{section_min_minutes}}, {{words_per_minute}}, {{max_questions}}, {{row_searches}}, {{row_fetches}}

Output: the whole outline, JSON per outline.SCHEMA.
---
This is the outline of a narrated audio chapter made for one listener. Its opening is written from a scout's notes alone; each later section waits on research that its research questions start. Two editors have settled its angle and its arc. A code check then found that it breaks these rules:

<problems>
{{problems}}
</problems>

The outline:

<outline>
{{outline}}
</outline>

The scout's notes. Their entries are {{scout_numbers}}, the only entries that exist yet:

<scout>
{{scout}}
</scout>

The rules, all of which the outline must keep:
- {{min_sections}} to {{max_sections}} sections, numbered from 1 in order; each title at most six words.
- Target words add up to between {{min_words}} and {{max_words}} (about {{target_words}}, at {{words_per_minute}} words a minute); section 1 at least {{opening_min_words}} ({{opening_min_minutes}} minutes); no section under {{section_min_words}} ({{section_min_minutes}} minutes).
- Every section has a non-empty lands, sets_up and hand_off, one sentence each. A hand-off is a place to rest: a question left open or an image, never a promise of what comes next.
- Section 1 has no research questions, lists at least one scout entry, and lists only scout entries. It is written from those entries alone.
- Sections 2 onward have one or two research questions each; at most {{max_questions}} in the whole chapter. Each has an idea and a question, a type of deep-dive, missing or covered, and a tier_needed of 1 to 4. Each gets one research agent with {{row_searches}} searches and {{row_fetches}} page reads.

Mend exactly what is listed, and change as little else as you can: keep the angle, the through-line, the order of the sections and every field that breaks no rule, word for word. Where a problem can be mended in more than one way, choose the way that keeps the arc: to drop a research question, merge it into the one nearest to it; to lose a section, fold its idea into a neighbour; to move words, take them from the section that can best spare them.

Return the whole outline, every field, in the same shape.
