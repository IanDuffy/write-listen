# Library Timeline and Listening Notes: progress

Spec: `docs/library-timeline.md` (Ian, 2026-11-15). Branch `feature/library-timeline`. Checks for a person:
`NEEDS_HUMAN.md`. The overnight build's progress moved to `overnight/PROGRESS.md`.

## Build order

| Step | State |
| --- | --- |
| 1. Data: `moments.jsonl`, `playback.json`, the reader | Done (`overnight/timeline.py`, `/api/library`'s `timeline`, `POST /api/chapters/<run>/playback`) |
| 2. Demo seed | Done (`demo/library_demo.json`, `wl demo seed`) |
| 3. Library screen | Not started |
| 4. Chapter screen | Not started |
| 5. Capture | Planner outcomes done (step 1); the rest not started |

## Acceptance checks

| # | State |
| --- | --- |
| 1 | Pass: two builds hash the same; `listeners/ian/` hashes the same before and after (live, and `tests/test_library_demo.py`) |
| 2-8, 10, 11 | Not yet |
| 9, 12, 13 | Need a person (`NEEDS_HUMAN.md`) |

## Where the code didn't match the spec (the stop rule)

Written down here rather than worked around; what was done instead, if anything, is said for each.

1. **library.jsonl's format.** The spec describes items of kind chapter, conversation, question, mention with
   `date`, `topics`, `link`, `relation`. The library as built (overnight) has kinds work, person, topic with `title`,
   `creator`, `note`, `source`, and the planner, the memory pass and the Change session read those. Nothing was
   renamed. A mention is a work or person item; the demo adds `conversation` items (the scrubber's first
   conversation) and the optional `date`, `topics`, `relation`, all additive.
2. **"Checked against the web as now", "linked as the library does now".** Nothing checks a mentioned work against
   the web, and library.jsonl holds no links (references.json in a chapter has URLs, the library doesn't). Not
   built; the demo's works carry no link.
3. **The chapter id for "became a chapter".** The planner writes the card before any chapter folder exists (the
   build names it when it locks the card), so `outcome_ref` is the card's plan id; the reader resolves it (still up
   next: "tonight's chapter"; built: that chapter, by its `00_source/up_next.json` id).

## Decisions

- **Demo dates and lengths.** The two real chapters keep their real audio, so their lengths are real: *What Beauty
  Asks of Us* is 43 min (the sketch says 38) and *Herons* 17 min (21). The sketch's mention at 17:30 is past the end
  of the herons audio; it sits at 16:40. The sketches' weekdays are not 2026's (Nov 14, 2026 is a Saturday); the
  screens show the real weekday.
- **Demo filler.** Seven cards from the real planner (one pass a night in a scratch listener, $0.85 in all), each
  from a moment or note in the chapter before where the planner took a seed; reviewed by Claude in a fresh context
  (two anachronisms, three factual overstatements and a typo fixed by hand; every work real, with its author).
- **The scheduler leaves the demo alone**: `settings.json` `"demo"` makes the tick do nothing, so switching to the
  demo with `wl overnight wait` running never plans or builds for it.
- **A replaced card's seed goes back in the pool** (`library.mark_unused`), only when a moment came from it: its
  moment reads Saved again, which should mean the planner can take it again.
