# Earpiece view: how to finish it

Branch `feature/earpiece-view`, cut from `feature/tester-demo` at e4772d3. It holds nothing but this file. The build
was stopped on 2026-11-20 after its design step, before any code was written: no code here is half done.

## What Ian asked for (2026-11-20)

"it would be great to see what the director is feeding to the narrator in real time. Any way to show a floating
window of that for an admin view? Obviously just for me in some kind of admin view (could be a toggle in the feedback
admin), but I would like to retain a history with timestamps that the notes are passed to the narrator as well to
understand how it affects the conversation"

## What to build (the lead's decisions)

1. **Toggle, off by default, per tester.** tester.json `earpiece_view`, set on the admin page (like "End demo:
   auto/show/hide" and "Chapter: real/rehearsal") and by `wl study create ... --earpiece-view`. Off demo mode, config
   `live.director.view` (default false). The page learns it per request (the tester's own flag, e.g. in the
   `/api/live/start` answer), never from the global `/live.js` settings. A friend's page never shows or loads it.
2. **Floating panel** (`page/earpiece.js`, loaded only when the flag is on; fed by a small hook in page/live.js so it
   cannot change behaviour). Newest first, every look: m:ss since the conversation started and clock time, trigger, who
   was speaking, the read, the plan as a checklist (changes marked), next; for a note: the note, kind/about, its fate
   (sent at m:ss / dropped: reason / held, then sent), and the guide's next line after a sent note. Errors and the
   director going down shown. Folds; never covers the circle, End demo, the play bar or the text box; phone width.
3. **History.** The page posts each note's fate (`POST /api/live/direct/fate {session, id, fate: sent|dropped|held,
   reason, at}`, 409 for a session that isn't current), recorded in the session transcript as a director row field,
   never `text` (so earlier_text, memory, explained() and whisper_in never read it). `directorVerdict` needs to return a
   reason for a drop.
4. **Admin history view.** From the admin page, per tester: their conversations, and for one a timeline interleaving
   the listener's and the guide's lines (m:ss and clock time) with each look, each note with its fate and when it
   reached the guide, and the guide's next line after it. Same auth as the other /admin routes.

The full contract the design step wrote (file ownership, names, shapes, tests) is in
`docs/earpiece-view-design.txt` beside this file. Check it against the code before building: it was written by reading
e4772d3.

## How to finish

1. `git switch feature/earpiece-view` (or `git pull` it in the cloud).
2. Build server side and page side per the contract, with tests.
3. Tests: only the files that cover it (Ian, 2026-11-20): `tests/live/test_director.py`,
   `tests/live/test_live_server.py`, `tests/demo/test_admin.py`, `tests/demo/test_testers.py`,
   `tests/demo/test_state.py`, and in `tests/live/test_live_page.py` only the new tests and `-k "director or earpiece"`
   (the whole file takes about 24 minutes). Not the full suite.
4. Check offline: run the server with `WL_DEMO=1` on a spare port, one tester with the view on and one off, the page in
   headless Chromium with the tests' fake GPT-Live and a fake director. No paid calls are needed.
5. Update CLAUDE.md (the Tester Demo section's earpiece bullet), then commit and push:
   `git push -u origin feature/earpiece-view`. Merge it into `feature/tester-demo` only when Ian says so.

In a cloud checkout `listeners/`, `shared/` and `.env` are missing. That doesn't matter for this work (the tests make
their own), but the rehearsal source (`shared/rehearsal/`) won't be there.
