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
| 5. Capture | Done: an ask's moments from the after-conversation pass, `save_seed`'s note, the planner's outcomes, `playback.json` from the player (gap 4 below) |

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
| 9 | Needs a person (`NEEDS_HUMAN.md`). Offline: two asks make two moments at their places with words and outcomes (`tests/test_library_capture.py`); live: the pass on two of your real asides ($0.023 each) turned "Um, I've heard that in the / Himalayas there are saints..." into one saved question with its seed |
| 12, 13 | Need a person (`NEEDS_HUMAN.md`) |

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

4. **Asks didn't get the after-conversation pass.** The spec has the pass "that already updates the profile and
   library" write an ask's moments; but an ask on a recorded chapter (overnight/live.py, mode ask) ended without
   it (the overnight build left it out; its seeds were written by code, `aside_seeds`). What I did: asks now get
   the pass (`overnight.live.memory_on_asks: true`; false puts it back as it was), and the pass returns `moments`
   for an ask only. Cost: about $0.02 an ask (Opus). Effects beyond moments: an ask can now update `profile.md`,
   `guide.md` and the library like any conversation; an ask that ends after a later conversation was remembered
   still gets its moments but leaves the profile alone. Your call whether this is the right fix; the alternative
   is a smaller pass for asks alone.
5. **Where capture doesn't reach.** Questions and mentions are captured for asks on the library page's recordings
   (GPT-Live, overnight/live.py). The older ElevenLabs asides (`ask/routes.py`, `?chapter=`) get `save_seed`'s note
   but no pass; questions asked during a chapter read live on the orb page (`page/live.js`, one GPT-Live session)
   are not asides at all and leave no moments. The orb page's own player doesn't write `playback.json`; the
   library's player does.

## Decisions

- **Capture.** A moment from an ask sits at the tap position (`position_s`, and `sentence_id` and `paragraph_id`
  `segNN-pKK` from the timed transcript, written into the session's first line). Questions are shortened into a
  line in the listener's words by the pass, with the guide's answer in one line (`reply`) when it answered;
  otherwise Saved, and the question becomes a seed (kind question). `save_seed` writes a note at once, naming the
  seed the aside gives it; if the pass then finds the question the guide saved, the note becomes that question
  (one ask, one moment). A mention goes into `library.jsonl` (a work) and reads In your library. For an aside the
  pass's own question seeds are dropped (the moments make them), so the planner never sees one question twice.
- **"Use"** needed nothing new: a card's source words come from its seed, which for a saved moment are the
  listener's question, and the intro brief already quotes the card's source words.

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
- **The Up next card** shows its title (three lines at most) and overview (two), and a small "Read more" that opens
  the summary and the listener's notes; it closes again when another card comes into view. The strip is as tall as
  the cards mostly in view (on a phone, the card in view), so a long Up next never pushes the scrubber down while
  another card shows (Ian, 2026-11-16). In view, "From this chapter" becomes "Where it came from".
- **A laptop (900 px and up)** gets the app's wide column (1,100 px, the transcript view's): cards 320 px, the one
  in view in the middle with its neighbours either side, the arrows beside the Library heading (never over a card),
  From this chapter in three columns, the chapter page at 720 px. The spec's 480 px column was replaced on Ian's
  look (2026-11-16). A phone is unchanged.
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

## Review (2026-11-16)

Two rounds. A single-context review (10 findings, 8 fixed). Then four independent reviewers, one per area (the
screens, the data, capture, the demo and tests), each finding put to a skeptic who tried to refute it: 23
candidates, 12 confirmed, all fixed with a regression test:

- Screens: a day's chapters sat on one scrubber point (now side by side, in the order they finished); Play on the
  chapter page started the live intro with nothing on screen (the line now shows there too, and Play reads "Skip
  to the chapter"); the transcript's Play could stay hidden on the latest chapter after a reload; a poll while a
  chapter played rebuilt the moments (closing an open "Play from") and the strip each minute (status lines now
  change in place); the page said Not started where the card said 1 of N min.
- Data: an open question after_chapter restated (it names the aside, not the session) never found its moment
  (moments now carry the aside id), and after_chapter no longer re-seeds an aside the pass already captured; with
  overnight off, a planned card's moment read "Became a chapter".
- Capture: an ask's question was seeded twice (the code's raw words and the pass's tidied ones: with the pass on
  for asks, only the pass's); an ask's pass could make a guide conversation still open at the time look "older
  than one already remembered" and skip it (asks no longer count in that ordering); a note was taken over by any
  saved question (now only one that shares its words); a chapter read live on the orb page got notes at 0:00 (no
  longer); a person mentioned was filed as a work too.
- A flaky test, flaky before this branch too (the read-along takeover read the position before the seek landed):
  it now waits for the seek.
