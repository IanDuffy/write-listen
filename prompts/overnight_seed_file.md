# Seed file template for an overnight chapter (not sent to a model on its own; named _file, not _seed: a *_seed.md prompt is a seed-run variant of another prompt, tests/test_seed_run.py)

Inputs:
- {{title}}, {{overview}}, {{summary}}, {{angle}}: from the Up next card (up_next.json)
- {{questions}}: the card's research questions, one "- " line each
- {{said}}: what the listener said that the chapter answers, word for word (the card's source.words, or the
  request's topic)
- {{source}}: where it came from (decided, kind and ref)
- {{notes}}: the listener's include or avoid notes (request or queued topic), or "(nothing)"
- {{added}}: notes the listener added to this plan afterwards (the card's notes_added: typed under the Up next
  card, said in Change, or left for the next chapter), one "- " line each, oldest first, or "(none)"

Written to 00_source/seed.md by the overnight build's lock stage (overnight/planner.seed_text). The seed-run
prompts (scout, ledger, brief, draft) read it whole, as they read prompts/seed_file.md's shape: keep the
"# Seed", "## Topic" and "## What the listener said, in their own words" headings.
---
# Seed

## Topic

{{title}}: {{overview}}

## The promise (the Up next card's summary; the chapter must keep it)

{{summary}}

## Angle

{{angle}}

## Questions to research

{{questions}}

## What the listener said, in their own words

{{said}}

## Where it came from

{{source}}

## Include or avoid

{{notes}}

## Notes the listener added to this plan (follow them: they came after the plan, and win where they differ)

{{added}}
