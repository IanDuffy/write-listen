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
| `record.py` | GPT-Live, server-side, one paragraph at a time; check each take, re-record up to 3 times; up to 4 sessions in parallel | `run(r, cfg, session_factory=None) -> dict` (writes `10_audio/paragraphs/`, `10_audio/takes/`, `10_audio/recording.jsonl`, `10_audio/recorded.json`) |
| `assemble.py` | stitch the audio, time it (Whisper), references, `episode.json` | `run(r, cfg) -> Path` (writes `10_audio/chapter.mp3`, `11_episode/timing.json`, `11_episode/episode.json`) |
| `intro.py` | `intro_brief.md`, the only things the morning intro may use | `run(r, cfg) -> Path` |
| `runner.py` | `wl overnight run|resume|status`: lock, stages, status.json, cost.json, runaway stop | `run(cfg, listener, *, now=False, ...)`, `resume(...)` |
| `commands.py` | the `wl overnight ...` CLI | `main(argv)` |
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
| 1 lock ("Locking the plan") | `00_source/up_next.json` | the card copy, `00_source/seed.md` (from the card, `prompts/overnight_seed.md`), card status building |
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

## Rules every piece keeps

- Costs: every paid call goes through `stages.llm.call` with the chapter `Run` (Claude) or logs
  `{"provider": "openai", "stage": ..., "cost_usd": ...}` to the run's `log.jsonl` (GPT-Live), so `Run.spent()`
  is the whole cost and `Run.max_usd` (the runaway stop) holds. Check `run.spent() >= run.max_usd` before
  each GPT-Live paragraph.
- Resume: a stage skips work whose file exists; partial work is kept per item (a paragraph, a section).
- Prompts only in `prompts/*.md` (`load_prompt`), inputs above `---`.
- No listener is waiting: no fast-start shortcuts (holds, solo locks, opening from the scout alone).
- Tests offline: fake Claude (`tests/fakes.py` patterns), a fake GPT-Live session, real ffmpeg.
