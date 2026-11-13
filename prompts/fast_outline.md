# Prompt: fast outline (Fast Start step 2, first call)

One call does what choose_seed.md, ledger_seed.md and brief_seed.md do in turn for
an overnight run: it picks the angle, sets the arc section by section, and writes
the research questions that become the ledger. In a fast run the outline comes
before the research, and nothing later can reorder the chapter, so the arc is
decided here. A second editor (fast_outline_edit.md) challenges it once.

Inputs:
- {{seed}}: 00_source/seed.md (and notes.md), what the listener said
- {{profile}}: listener/profile.md (if any)
- {{notes}}: listener_notes.md so far, what they have said since the topic was set
- {{scout}}: 01_scout.md: the territory sketch, the entries numbered D01..., the scenes
- {{scout_numbers}}: the scout's entry numbers, the only ones that exist yet
- {{min_sections}}, {{max_sections}}, {{target_words}}, {{target_minutes}}, {{min_words}}, {{max_words}},
  {{opening_min_words}}, {{opening_min_minutes}}, {{section_min_words}}, {{section_min_minutes}},
  {{words_per_minute}}: the chapter's sizes, config.yaml fast
- {{max_questions}}, {{row_searches}}, {{row_fetches}}: the research budget, config.yaml fast.research
- {{source_tiers}}: prompts/source_tiers_seed.md

Output: JSON per outline.SCHEMA (structured output). Code checks it (outline.validate).
---
You are the commissioning editor for one narrated audio chapter, about {{target_minutes}} minutes for a single narrator, made for one listener from a topic they asked about. They are curious and not a specialist. Nobody reviews your plan before the chapter is made; a second editor will challenge it once, and then it is fixed.

This chapter is made while the listener is already listening. The opening is written and narrated straight away, from the scout's notes below and nothing else. Research for the later sections starts the moment your outline is done and runs while the opening plays; each later section is written, edited and narrated just before the listener reaches it. So the arc is decided here. Once the opening has played, nothing can be reordered, and no section can lean on research that will not have arrived.

The chapter succeeds only if the listener:
1. finishes it without skipping;
2. learns something they did not expect, and could not have found in the first page of search results;
3. feels it was made for them: it answers what they asked and why they asked it;
4. wants more: keeps thinking about it afterwards (earned by the chapter itself, never by previewing another);
5. hears no invented facts (every claim is checked against a researched source).

What the listener said:

<seed>
{{seed}}
</seed>

What they have told us about themselves before, if anything:

<profile>
{{profile}}
</profile>

What they have said since asking, if anything. It may shape emphasis and the choice of example:

<listener_notes>
{{notes}}
</listener_notes>

The scout's notes: a sketch of the territory, numbered entries (each tagged with its kind, its source tier and, for quotes, whether it is verbatim), and two or three scenes. The entries are {{scout_numbers}}; no others exist yet.

<scout>
{{scout}}
</scout>

Source tiers:

{{source_tiers}}

Write the outline. Keep every field as short as it is asked for below: the writers need a plan, not prose, and the listener is waiting while you write.

## The angle

- **title**: the chapter's title, a few words.
- **angle**: the one claim the chapter argues, in two or three sentences. A claim is something a person could disagree with, not a topic. Choose it for whether it answers what the listener asked and why they asked; whether the scout shows real, strong sources for it (the territory sketch says what is contested or thin); whether it has a scene and a turn a listener would stay for; and whether it fits in {{target_words}} words, which hold three or four ideas, not more.
- **why**: two or three sentences: why this angle for this listener, and what it leaves out.
- **through_line**: the one question or tension that carries the listener from the first section to the last, in one or two sentences. The opening asks it, each section after moves it on with something new, and the last answers it; no later section is planned to ask it over again. The chapter is an argument with a shape, not a tour.
- **closing_idea**: what the last lines leave the listener with, in one or two sentences: an image or an open question that keeps working after the audio stops. It comes after the chapter's answer and does not put the through-line again in other words. It may call back to the opening scene. The chapter stands alone, so it never previews another.
- **listener_words**: two or three of the listener's own phrases, copied exactly from what they said, for the opening to echo within its first minute, once the scene has begun. Their words only, never a paraphrase. Leave it empty if they gave only a bare topic.

## The sections

{{min_sections}} to {{max_sections}} sections, numbered from 1, in the order the listener hears them. You decide how many: one idea each, so three strong sections beat five thin ones.

Sizes, at {{words_per_minute}} words a minute. Code checks these and rejects an outline that breaks one:
- The targets add up to about {{target_words}} words, and never less than {{min_words}} or more than {{max_words}}.
- Section 1, the opening, is at least {{opening_min_words}} words ({{opening_min_minutes}} minutes): it has to play long enough for the research behind section 2 to arrive.
- No section is under {{section_min_words}} words ({{section_min_minutes}} minutes).
- They need not be equal. Give the hardest idea the most room.

For each section:
- **n**: its number.
- **title**: two to five words, shown on the page as the section's marker while it plays. It names the section without giving away its turn.
- **lands**: the one idea the listener holds when the section ends, in one sentence. An idea is a claim or a move in the argument, something they could repeat to a friend. "Roman sea concrete grows stronger as seawater gets into it" is an idea; "Roman concrete" is a topic. In sections 2 onward, an idea no scout entry yet bears out is worded as what the section settles (see the last paragraph below).
- **sets_up**: what it plants that a later section pays off, in one sentence: a concrete thing the listener will meet again (a detail of the scene, a claim someone made, a question), never the through-line or the angle in other words. For the last section: which earlier set-up it pays off.
- **hand_off**: the question or image in its last lines that pulls the listener into the next section, in one sentence. For the last section: how it ends on the through-line. Each section is narrated before the next is finished, and the listener may wait at the boundary, so a section's last lines must be a place to rest: a question left open, never a sentence that needs the next section to complete it, and never a promise of what comes next.
- **target_words**: a number.
- **notes**: how to tell it, in two to four sentences: the scene it opens on, the order of its moves, what to hold back for a later section.
- **scout_entries**: the numbers of the scout entries the section uses.
- **research_questions**: see below.

## Section 1, the opening

It is the introduction and the first section together, and it is written from the scout alone.

- Make it story-led: the scout's strongest scene, told in full, and the question the chapter will answer. The later sections get research of their own and the opening gets none, so it has first call on the scout's other scenes: plan a second as the turn unless telling it would start to answer the question. A later section that comes back to a scene the opening told plans what its own research adds, not a retelling. It does not start answering the question; that is what the research is for.
- Its first lines are the scene. Within its first minute the listener also knows what they are listening to, how it grows from what they asked, and the question the chapter will chase. Say in the notes where in that first minute, after the scene has begun, it ties back to their words.
- Every fact in it comes from a scout entry. It carries few facts only because the scout gives it few: plan every entry of its scenes that does not answer the question, with the names, dates, places and numbers it gives. List in scout_entries every entry it uses, and plan nothing the entries do not state. Plan a quotation only from an entry marked verbatim: yes. A second-hand entry (a written work quoted or reported by a page that is not the work) is planned as that page's report, since nobody has yet read the work: the notes name the page, and the lands, sets_up and hand_off state nothing about what the work says or is about beyond what the entry gives.
- What its argument rests on comes from tier 1 or 2 entries; a tier 4 entry is color only.
- Its research_questions list is empty.
- At {{opening_min_words}} words or more it is long for what the scout gives it, and wherever the writer has nothing to tell, it asks the question again. Plan enough moves to carry that length without padding. A move tells the listener something from an entry: a scene with every detail its entries give, a person, a number, a quotation. Told aloud, a move carries about half a minute, a full scene about a minute. What most people assume, and the question put again in new words, have no entry behind them and carry nothing. For this section write the notes as the moves in order, a few words each with its entry numbers, the tie to the listener's words in its place, then what to hold back. Count the moves against the length, and if they fall short bring in another scout scene or entry that does not answer the question. The question is put once, inside the first minute, and may come back once, in a sentence, before the hand-off. Tie it to what the listener said, in their words, and make no guess about their life, mood or feelings. Not a survey of what is to come.

## Sections 2 onward

Each has one or two research questions, and the chapter has at most {{max_questions}} in all. Each question gets one research agent with {{row_searches}} searches and {{row_fetches}} page reads, and there is no second round: a question too broad for that budget comes back thin, and its section is written thin. For each:

- **idea**: one sentence stating what the section must make the listener understand: a claim where a scout entry already bears it out, otherwise what is to be settled. Never a bare topic.
- **question**: the one specific question research must answer, narrow enough to settle within that budget: name the primary text and passage, the person, the event or the kind of source that would settle it. Ask for what would make the section tellable, not only true, and for what the scout lacks: a question that asks again what a scout entry already reports brings back the same report, so ask for the measurement, method or passage behind it. Where the listener said what they do or know and it bears on a section's idea, one of that section's questions that is not "covered" asks for the answer in the terms of that work: its test, its measure, its practice, with figures. For a "covered" question, list instead the specific factual claims from the scout that the section would repeat, numbered, each with its entry number; each is checked, because popular sources repeat errors in dates, names and attributions.
- **type**: "deep-dive" if the scout found it but only on the surface; "missing" if the angle needs it and the scout found nothing; "covered" if the scout's sources already settle it and research only checks the claims.
- **tier_needed**: the lowest source tier that would settle it. Anything a section's argument rests on needs tier 1 or 2.
- **scout_has**: what the scout already has for it, citing entry numbers, as leads for the agent. Empty if nothing.

Section 2's research has the least time: its section is written while the opening is still playing. Give section 2 the questions the scout already points to sources for, and put the questions that start from nothing later in the chapter. Two questions that would send research to the same pages are one question.

A later section may also list scout entries it will use. It may not count on any other fact until its research is back, and your outline is a plan, never a source: the writers are told to land what you write here, and may state only what an entry states. So in a later section's lands, idea and notes, state as fact only what a scout entry already gives, and put the rest as what the section settles: "whether the builders chose seawater on purpose, and how we know", not "the builders chose seawater on purpose". Name no date, number, name or place that no scout entry gives. Plan its moves around the question, not around an answer you expect.
