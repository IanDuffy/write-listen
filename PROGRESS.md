# Overnight Chapters: progress

Spec: `docs/overnight-chapters.md`. Branch `feature/overnight`. Design and contracts: `overnight/DESIGN.md`.
Build costs per check: `overnight/build_costs.jsonl`.

## Build order

| Step | State |
| --- | --- |
| 1. Profiles and storage | Done. `listeners/<id>/`, one resolver (`stages/common.listener_dir`, `chapters_dir`), settings, reset (new demo listener), migration (copies). |
| 2. Planner | Next |
| 3. Overnight runner | |
| 4. Library page | |
| 5. Morning playback | |
| 6. Change by voice | |

## Acceptance checks

| # | Check | State |
| --- | --- | --- |
| 1 | Existing chapters and sessions open from `listeners/ian/` after migration | **Pass.** `wl listener migrate` copied the main checkout's runs and listener and the live-voice worktree's herons chapter: `chapters/2026-11-11-beauty-scarry`, `chapters/2026-11-14-herons-hunt-standing-still`, everything else under `archive/<checkout>/` (43 session transcripts). Both chapters open through the server (status ready, episode, ask, audio). Test: `tests/test_overnight_profiles.py::test_migration_copies_everything_and_the_chapters_open`. |
| 2 | Reset creates a demo listener; `ian/` byte-for-byte unchanged | **Pass** (`test_reset_makes_a_demo_listener...`, `tests/test_server.py::test_start_fresh_...`). |

## Notes

- Ian's live profile was empty at migration: every earlier `listener/` had been reset into `_cleared_*` folders,
  which are kept under `listeners/ian/archive/`. Nothing was made active from them.
- Migration copies, never moves: the other checkouts are other sessions' working folders.
- Voice agent ids (ElevenLabs) moved to `shared/voice/`, so a new listener doesn't create new agents.
- Probe (2026-11-15, $0.02): a server-side GPT-Live session needs no `delegation`; muted, it read one paragraph
  sent as commentary word for word.
