# Library Timeline and Listening Notes: progress

Spec: `docs/library-timeline.md` (Ian, 2026-11-15). Branch `feature/library-timeline`. Checks for a person:
`NEEDS_HUMAN.md`. The overnight build's progress moved to `overnight/PROGRESS.md`.

## Build order

| Step | State |
| --- | --- |
| 1. Data: `moments.jsonl`, `playback.json`, the reader | Done (`overnight/timeline.py`, `/api/library`'s `timeline`, `POST /api/chapters/<run>/playback`) |
| 2. Demo seed | Done (`demo/library_demo.json`, `wl demo seed`) |
| 3. Library screen | Done (`page/library.js`: strip, scrubber, From this chapter, Up next with Change and notes) |
| 4. Chapter screen | Done (`#/chapter/<run>`; the read-along moved to `#/chapter/<run>/transcript`) |
| 5. Capture | Planner outcomes and `playback.json` from the player done; the ask capture: see below |

## Acceptance checks

| # | State |
| --- | --- |
| 1 | Pass: two builds hash the same; `listeners/ian/` hashes the same before and after (live, and `tests/test_library_demo.py`) |
| 2 | Pass (`tests/test_library_timeline_page.py`, and by eye on the demo) |
| 3 | Pass: arrow keys, the arrow buttons (laptop width) and a touch swipe (a synthesized gesture) each move one card; the handle follows |
| 4 | Pass: dragging the handle, and tapping a chapter's or a moment's tick |
| 5 | Pass: the demo's Oct 24 to Nov 3 gap is drawn as three days with a break mark |
| 6 | Pass |
| 7 | Pass: marker centres within 2% of the bar; a tap scrolls to the moment and, on the latest chapter, offers "Play from" ten seconds before |
| 8 | Pass: Play only on the latest card and its chapter page; older transcripts read but don't play |
| 10 | Pass: the planner marks the moment (seen live in the demo's generation, 7 of 7 seed-sourced cards) and a replaced card puts it back to Saved, on both screens |
| 11 | Pass: the start of a play, every 10 s, pause and the end are posted; after a reload the card's status line and the page's bar match `playback.json` |
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

- **Visual tokens.** The spec keeps the app's token where it already sets one. The app sets the font (the system
  font), the ground (`--bg` #fafaf8), ink (`--fg`), muted (`--muted` #77756f), line (`--line`) and the accent
  (`--accent` #d9822b, orange), and supports dark mode; the screens use those. From the sketches: surface (white,
  as the library already had), accent text #9C4330, the three topic colours, the card and row radii, sizes and
  layout. So the screens are in the system font and the app's orange, not Instrument Sans and coral; switching is
  six variables at the top of the library's styles. White text on the amber card fails contrast, so amber cards
  use ink text.
- **Topic colours** go by each main topic's first chapter, in order (teal, coral, amber, then round again). A
  chapter's main topic is its card's `topics[0]` (the demo's cards have them; the planner doesn't write topics yet),
  else its title, so real chapters each get the next colour.
- **Only the latest chapter plays**, on both screens and in its transcript (the overnight round 2 let any chapter
  play; the spec says latest only). The library's Play button is `#lib-play`: the orb page has its own `#play`, a
  clash the old library had too.
- **The Up next card** is taller than the chapter cards when its summary is long (the cards align to the top rather
  than all stretch). In view, "From this chapter" becomes "Where it came from": the moment the card came from.
- **The chapter page's Play** starts the morning flow, as the card's does; a marker's "Play from" plays the recording.

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
