# Overnight Chapters: design and contracts

Spec: `docs/overnight-chapters.md` (Ian, 2026-11-15). This file fixes the modules, files and formats so
the pieces can be built in parallel and fit together. The report (`overnight/REPORT.md`) records what
was built and checked.

## Modules (package `overnight/`)

| Module | Job | Entry points |
| --- | --- | --- |
| `profiles.py` | listener folders, settings, reset, migration, chapter listing | `create`, `set_active`, `reset`, `settings`, `save_settings`, `migrate`, `chapters`, `latest_chapter`, `read_json`/`write_json`/`append_jsonl`/`read_jsonl` |
| `schedule.py` | pure clock rules: build days, cutoff, when to plan, when to build | `next_build(settings, now)`, `plan_due(card, settings, now)`, `build_due(card, settings, now)`, `can_change(card, settings, now)` |
| `planner.py` | the 18:00 planning pass: modes, queue, `up_next.json` | `plan(cfg, listener, *, request=None, now=None) -> dict` |
| `library.py` | `library.jsonl` / `seeds.jsonl`: reading, adding, marking used; what a finished chapter adds | `seeds(listener, unused=True)`, `add_seeds`, `add_works`, `mark_used`, `after_chapter(cfg, listener, chapter_root)` |
| `outline.py` | the outline written **after** research, from the full dossier | `run(r, cfg) -> dict` (writes `05_outline.json`, `05_outline.md`, `05_brief.md`, sets each ledger row's `section`) |
| `write.py` | write and edit section by section with the paired edit (reuses `faststart.sections`) | `run(r, cfg) -> Path` (writes `06_segments/segNN.md`, `07_script_draft.md`) |
| `promise.py` | check the script against the card's summary; one revision if it drifted | `run(r, cfg) -> Path` (writes `08_promise.json`, `09_script_final.md`) |
| `record.py` | GPT-Live, server-side, one paragraph at a time; each take transcribed by local Whisper and compared with the script (GPT-Live's own transcript as the tie-breaker for names), re-recorded up to 3 times; up to 4 sessions in parallel | `run(r, cfg, session_factory=None, recognizer=None) -> dict`, `preflight(cfg)` (writes `10_audio/paragraphs/`, `10_audio/takes/`, `10_audio/recording.jsonl`, `10_audio/recorded.json`) |
| `assemble.py` | stitch the audio, time it (Whisper), references, `episode.json` | `run(r, cfg) -> Path` (writes `10_audio/chapter.mp3`, `11_episode/timing.json`, `11_episode/episode.json`) |
| `intro.py` | `intro_brief.md`, the only things the morning intro may use | `run(r, cfg) -> Path` |
| `runner.py` | `wl overnight run|resume|status`: lock, stages, status.json, cost.json, runaway stop | `run(cfg, listener, *, now=False, ...)`, `resume(...)` |
| `commands.py` | the `wl overnight ...` CLI | `main(argv)` |
| `locks.py` | the card lock (`listeners/<id>/.card.lock`, fcntl): the runner taking a card and the planner writing one never cross | `card_lock(listener)` |
| `live.py` | the library page's GPT-Live sessions: the morning intro, Change, an aside on the recording | `mount(app, cfg, pipe, chat)` |
| `routes.py` | `/library`, `/api/library`, `/api/settings`, the intro and change live sessions | `mount(app, cfg, pipe, chat)` |

## Listener files

All under `listeners/<id>/` (`stages.common.listener_dir(cfg)`); JSON written whole (`profiles.write_json`).

**`up_next.json`** (the card):
```json
{"id": "plan-20261116-180012", "for_morning": "2026-11-17",
 "title": "Why Herons Wait", "overview": "one line",
 "summary": "three or four sentences: the question, the angle, why it follows from the listener",
 "mode": "surprise|continue|topic", "decided": "continue|branch|topic|queue",
 "source": {"kind": "chapter|seed|request|queue|profile|library", "ref": "<chapter folder, seed id, session id>", "words": "the listener's words, if any"},
 "research_questions": ["..."], "angle": "...",
 "request": {"mode": "topic", "topic": "...", "notes": "...", "session": "<id>"} ,
 "status": "planned|building|ready|failed", "planned_at": "...", "locked_at": null,
 "chapter": null, "error": null, "plan_dir": "plans/plan-20261116-180012"}
```
`request` is null unless the plan came from Change by voice. `chapter` is the chapter folder's name once the
build locks it. `status` moves planned -> building -> ready, or -> failed.

**`queue.jsonl`**: `{"id": "q-<time>", "t": "...", "mode": "topic", "topic": "...", "notes": "...", "session": "..."}`.
A queued topic takes the next planning pass ahead of the mode; when used, `{"used": "<id>", "by": "<plan id>", "t": ...}`
is appended (append-only).

**`seeds.jsonl`**: `{"id": "s-<hash>", "t": "...", "kind": "question|idea|parked", "text": "...", "words": "listener's words or null", "source": {"chapter": "...", "session": "...", "aside": "..."}}`;
used: `{"used": "<id>", "by": "<plan id>", "t": ...}`.

**`library.jsonl`**: `{"id": "w-<hash>", "t": "...", "kind": "work|topic|person", "title": "...", "creator": "... or null", "note": "...", "source": {...}}`.

**`plans/<plan id>/`**: `log.jsonl` (the plan's calls, priced), `inputs.md` (what the planner read), `choice.json`,
`scout.md`, `card.json`.

## Chapter folder (`chapters/<for_morning>-<slug>/`)

The seed-run layout, so the episode page and ask-by-voice read it unchanged:

| Stage (status label) | Done when this exists | Writes |
| --- | --- | --- |
| 1 lock ("Locking the plan") | `00_source/up_next.json` | the card copy, `00_source/seed.md` (from the card, `prompts/overnight_seed_file.md`: a `*_seed.md` name would read as a seed-run prompt variant), card status building |
| 2 research ("Researching") | `04_research/ledger_update.md` | `01_map.md` (scout), `03_angle.md` (from the card), `ledger.json`, `04_research/`, `04_dossier.md` |
| 3 write ("Writing") | `07_script_draft.md` | `05_outline.json/.md`, `05_brief.md`, `06_segments/`, `07_script_draft.md` |
| 4 promise ("Checking it against the card") | `09_script_final.md` | `08_promise.json` (and one revision) |
| 5 record ("Recording") | `10_audio/recorded.json` | `10_audio/paragraphs/sNN-pKK.wav`, `10_audio/takes/`, `10_audio/recording.jsonl` |
| 6 assemble ("Assembling") | `11_episode/episode.json` | `10_audio/chapter.mp3`, `10_audio/concat.txt`, `11_episode/timing.json`, `references.json` |
| 7 intro ("Writing the intro brief") | `intro_brief.md` | |
| 8 finish ("Finishing") | `finished.json` | library and seeds, `history.md`, card ready |

Also: `status.json` (stage, stage start, cost so far, error; the server's fields), `cost.json`
(`{"total_usd", "by_stage": {...}, "by_provider": {...}, "runaway_stop_usd"}`), `log.jsonl` (every call),
`decisions.md`, `run.log`.

Paragraph ids: `s<seg>-p<kk>` (`s1-p01`), from `09_script_final.md`: spoken text (no `[Dn]`), split at blank
lines; a `[pause]` line ends a paragraph with a beat. The same split the pause rules use in assembly.

## The card's lifecycle, across processes

- The scheduler's tick (launchd, every quarter hour) and the server (Change) are different processes. The
  runner moves a card to building, and the planner writes up_next.json, only under `locks.card_lock`; the
  planner re-reads the card before writing: a Change whose cutoff passed during the pass is queued, not
  written; a plain pass whose card changed meanwhile is dropped.
- `runner.reconcile(listener)`: a card still `building` whose build process is gone (no live pid in
  `.overnight.lock` or status.json) becomes `failed` ("the build process stopped"); the tick, the runner,
  the planner and the library page call it first.
- A failed card is retried once, on a later build day than its last attempt (`attempt_at`); only scheduled
  attempts count (`scheduled_attempts`), so daytime `--now` tests never use up tonight's build.
- A failed planning pass is recorded in `plan_failures.jsonl`; the tick tries at most once an hour and three
  times a window, and the card says "Couldn't plan the next chapter". A queued topic that fails twice is set aside.
- Before a build spends anything, `record.preflight` checks local Whisper and its model are on the machine.

## Rules every piece keeps

- Costs: every paid call goes through `stages.llm.call` with the chapter `Run` (Claude) or logs
  `{"provider": "openai", "stage": ..., "cost_usd": ...}` to the run's `log.jsonl` (GPT-Live), so `Run.spent()`
  is the whole cost and `Run.max_usd` (the runaway stop) holds. Check `run.spent() >= run.max_usd` before
  each GPT-Live paragraph.
- Resume: a stage skips work whose file exists; partial work is kept per item (a paragraph, a section).
- Prompts only in `prompts/*.md` (`load_prompt`), inputs above `---`.
- No listener is waiting: no fast-start shortcuts (holds, solo locks, opening from the scout alone).
- Tests offline: fake Claude (`tests/fakes.py` patterns), a fake GPT-Live session, real ffmpeg.

## Round 2 (Ian's notes after the demo, 2026-11-15)

**One home page, one player.** The library is no longer a page of its own: it is a module mounted below the
main page's orb (scroll down: Up next, the latest chapter, then every past chapter). The module is
`page/library.js` with its markup built by script into `<section id="library">` and a fixed mini player
`<div id="player">` at the bottom; `page/index.html` (this branch's `/`) mounts it with
`<section id="library"></section>` and `<script src="/library.js"></script>`, and the live-voice page will do the
same when that branch lands. `/library` stays as a standalone shell around the same module.
- **Persistent playback:** the module owns one `<audio>` (`#chapter-audio`). Navigation inside the app is
  client-side (`location.hash`): `#/` home, `#/chapter/<run>` the chapter view (read-along transcript with the
  current sentence highlighted, click a sentence to seek, reference cards), built from
  `/runs/<run>/11_episode/episode.json`. Moving between them never touches the audio. Links to the old
  standalone episode page are replaced by `#/chapter/<run>`. When the page's own `#audio` (the orb's chapter)
  starts, the chapter player pauses, and the other way round.
- **History:** `GET /api/library` adds `history`: every finished chapter, newest first by when it finished
  (`profiles.chapters` + `_chapter_info`), each `{run, title, overview, date, minutes, audio, episode, intro}`.
  Any chapter plays; only the latest offers the live intro.
- **Play and the mic:** Play on the latest chapter opens the live intro (the guide needs the mic so the listener
  can answer); a "Skip the intro" link plays the recording at once. Every other Play just plays.

**Notes for the next chapter.** "Add a note" on the Up next card opens a text box. `POST /api/overnight/note
{"text"}` -> `{"where": "card"|"tonight"|"next", "note": {...}, "message": "..."}`:
- a planned card (before the cutoff): appended to the card's `notes_added` `[{"t", "text", "via": "typed"|"voice"}]`;
  `planner.seed_text` puts them in `00_source/seed.md` at lock, so research, outline and writing see them;
- the build has started but research hasn't finished: appended to the chapter's `00_source/notes.md` (which
  `common.seed_inputs` already reads) and to the listener's card;
- otherwise (too late tonight, or no card yet): `listeners/<id>/next_notes.jsonl`; the next planning pass takes
  them into its card's `notes_added` and marks them used.
`/api/library`'s card carries `notes_added`; the card shows them under the summary. Change by voice:
`set_next_chapter` with mode `keep` and notes adds them the same way (via "voice").

**Every live session knows the situation.** `stages/memory.meeting(cfg) -> {"first": bool, "chapters": n,
"conversations": n, "guide_name": str|None, "last_chapter": title|None}`: a first meeting only when the listener
has no chapter, no past conversation in which they spoke, and no guide.md. Every GPT-Live session (intro,
change, ask) gets `{{meeting}}` (a returning listener is never greeted as new: no "nice to meet you", no
introducing itself) and `{{situation}}` (why this session was opened: "They tapped Change on tomorrow's plan
..."; "They pressed Play on this morning's chapter ..."; "They tapped to ask during ...") and `{{recent}}` (the
last few lines of their latest conversation and the latest history entry). The opening notes say what to do
first in that situation, not "say hello". The onboarding guide (`stages/guide.py`) uses the same `meeting()` for
its returning flag. The page posts any line still being heard before it ends a session.

After the round-2 review: the orb's voice guide and the library's player and live sessions never run together
(`window.Orb.busy/end` from index.html, `window.Library.busy/end` from library.js); the chapter view takes over
the orb's `#audio` when it is playing the same chapter; Ask is offered only on chapters with a timed transcript
(`asks`). `{{recent}}` leaves out the session's own chapter and is capped (an ask gets no history entry);
`session_config` trims `{{recent}}`, then the dossier, rather than refusing; a conversation in which the
listener asked to forget something is marked `sessions/<id>.forgot` and never quoted back. Notes cover failed
cards too, a replaced planned or failed card's notes carry over, and notes added while research ran reach the
writer and the editor (`overnight/write.py` reads `00_source/notes.md`).
