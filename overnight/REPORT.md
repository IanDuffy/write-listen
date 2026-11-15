# Overnight Chapters: report

Spec: `docs/overnight-chapters.md` (Ian, 2026-11-15). Built 2026-11-15 on branch `feature/overnight` with
Opus 5.5. Design and contracts: `overnight/DESIGN.md`. Checks that need a person: `NEEDS_HUMAN.md`.

**In one paragraph.** It works end to end against the real APIs. `wl overnight run --now` planned
"Why Some Bridges Sway in the Wind" from a request, researched it on the full ledger path, wrote it in six
sections with the paired edit, checked it against the card, recorded it with GPT-Live in four parallel
server-side sessions, timed it, and wrote the intro brief: a 19.5-minute chapter for about $10, in about 45
minutes of building. The live run found a real defect that no offline test could: takes were cut by the
transcript's clock, which drifts seconds behind the audio, so most paragraphs lost their first words. Only a
Whisper check of the audio showed it. The cut is fixed, every take is now checked by Whisper, and the chapter
was re-recorded. The library page, the morning intro, asking during the recording and Change by voice are built
and tested in a browser with GPT-Live faked; they need a person and a microphone (checks 11-13).

## Acceptance checks

| # | Check | Result |
| --- | --- | --- |
| 1 | Existing chapters and sessions open from `listeners/ian/` after migration; nothing lost | **Pass.** `wl listener migrate` copied (never moved) the main checkout's runs and memory and the live-voice worktree's herons chapter: `chapters/2026-11-11-beauty-scarry`, `chapters/2026-11-14-herons-hunt-standing-still`, everything else under `archive/<checkout>/` (43 session transcripts). Both chapters open through the server (status, episode page, ask, audio). Tests: `tests/test_overnight_profiles.py`. |
| 2 | Reset creates a demo listener; `ian/` byte-for-byte unchanged | **Pass** (tests hash every file before and after; the page's button is now "Start fresh"). |
| 3 | Planner writes a card in each mode from real data; episode-style titles; no repeat | **Pass, live** on the migrated data ($0.10 a pass, about 40 s): surprise and continue both went deeper on the herons chapter from the listener's own question ("Where the Gray Heron Sits in Its Family", "The Gray Heron and the Wider Heron Family"); topic gave "Why Some Bridges Sway in the Wind". One summary ran to five sentences: the card now gets one retry past four. |
| 4 | A queued topic takes the next build ahead of the mode | **Pass** (tests). A queued topic whose passes keep failing is set aside after two. |
| 5 | `wl overnight run --now` produces a complete chapter folder | **Pass, live.** Audio, script, timed transcript, references, `episode.json`, intro brief, `status.json`, `cost.json`; the card went planned → building → ready. |
| 6 | Every paragraph's transcript matches its script after normalising; mismatches and re-records reported | **Pass, after a fix found by this check.** Every take transcribed by local Whisper: 50 of 50 paragraphs within two words, 43 word for word; 22 re-records over the re-recording. The first recording looked perfect on GPT-Live's own transcript but had lost openings (below). |
| 7 | 18-22 minutes; matches its card's summary | **Pass, live.** 19.5 minutes (3,270 words). The promise check kept every point of the summary (question, the felt sway, deliberate flexibility, vortex shedding, flutter, Tacoma, testing), so no revision was needed. |
| 8 | Kill mid-research and mid-recording; resume finishes without redoing finished stages | **Pass** (tests kill the build at a research row and at the fifth paragraph; resume repeats no paid call before the kill point and no recorded paragraph). Live, the build was also resumed after a stop at assembly: nothing before assembly ran again. |
| 9 | Overnight off: no build. Non-build day: no build | **Pass** (tests through `wl overnight tick` and `run`, and the pure schedule rules). |
| 10 | A real scheduled 2:00 build on the laptop finishes by morning | **Needs a person** (`NEEDS_HUMAN.md`): install the launchd entry and let it run a night. The live build took about 45 minutes. |
| 11 | Change before 2:00 rewrites the card within a minute; after 2:00 it queues and the guide says so | **Code passes; by voice needs a person.** A planning pass took 40-45 s live. Before the cutoff the card is rewritten; after it (or if the cutoff passes during the pass) the request goes to `queue.jsonl` and the tool's result tells the guide to say so (tests, including a Change at 01:59:30 written at 02:00:30). |
| 12 | The intro gives nothing beyond the brief; the chapter starts by tool or fallback every time (10 runs) | **Code passes; the 10 runs need a person.** The intro session gets `intro_brief.md`, never the script (a test checks no eight-word run of the script reaches it). The page starts the recording on `start_chapter`, or after the guide's lead-in line and 1.5 s of silence, or at 90 s (browser tests for all three). `python -m overnight.tally` counts how each ended. |
| 13 | Tap during a recorded chapter pauses, answers and resumes with the rewind rules | **Code passes; needs a person** (a browser test taps at 20 s and the recording resumes at the server's restart point from `ask/aside.py`). |

## What was built

| Step | Where |
| --- | --- |
| 1. Profiles and storage | `stages/common.py` (`listener_dir`, `chapters_dir`, a run pinned to its owner's folder), `overnight/profiles.py` (settings, reset, migration, chapters), `wl listener ...` |
| 2. Planner | `overnight/planner.py`, `schedule.py`, `library.py`; the memory pass also returns library items and seeds; `prompts/overnight_plan_*.md` |
| 3. Overnight runner | `overnight/runner.py`, `commands.py` (`wl overnight run/resume/status/plan/tick/schedule`), `outline.py`, `write.py`, `promise.py`, `record.py`, `assemble.py`, `intro.py`, `locks.py` |
| 4. Library page | `/library` (`page/library.html`, `overnight/routes.py`): Up next and the latest chapter, settings |
| 5. Morning playback | `page/library.js`, `overnight/live.py`, `prompts/overnight_voice_*.md`: the intro, the pivot, the fallback, asking during the recording, `playback.read_live` |
| 6. Change by voice | the same page and module: a Change session ending in `set_next_chapter` |

Commands: `./wl overnight run --now` (build now), `./wl overnight status`, `./wl overnight schedule show|install|remove`,
`./wl listener list|use|reset|migrate|settings`. `wl` is a script at the repo root running `cli.py` with the venv.

## Recording (checks 6 and 10 of the One Live Voice spec)

- Server-side GPT-Live (`wss://api.openai.com/v1/live/sessions`): no delegation needed (a $0.02 probe), mic muted,
  each paragraph sent as speakable context, one outstanding at a time. GPT-Live has no "finished" event, so a
  paragraph is done when its words have been heard in the output transcript and the audio is quiet.
- **The cut (found live).** The first live recording passed all 50 paragraphs on GPT-Live's own transcript. A
  Whisper pass over the audio then showed 34 of 50 missing their opening words: the transcript's `start_ms` ran
  seconds behind the audio over a five-minute session, more the later in it, and takes were cut there. Now a take
  is everything audible since the previous take (one passage is outstanding at a time and the buffer is emptied
  after each), trimmed to speech; the transcript's clock is not used to cut. A regression test runs the real
  client against a fake server whose transcript clock is 3 s behind.
- **The check** (spec: "transcribe each take and compare it with the script") is now local Whisper on each cut
  take, with GPT-Live's transcript as a tie-breaker for names Whisper spells its own way. The normaliser reads
  numbers in digits as the script's words (10.8, 1/60th, 1950s, 600-foot, 1st) and British/American spellings
  as one. A take passes with at most two words different, none touching a negation or a number, and nothing said
  before the passage. Up to three re-records, then the best take is flagged.
- **A pickup.** One paragraph ended before its last sentence in four takes running. When a take is right up to a
  sentence and only the sentences after it are missing, those are read as a passage of their own and joined at
  the sentence break.
- First (cut-early) recording: 50 paragraphs, 0 re-records, 4 sessions, $1.03, kept in the chapter as
  `10_audio_cut_early_20261115/`. Re-recorded with the fix: 50 paragraphs, 70 takes (20 re-records, 12 of them
  forced by number spellings the normaliser now reads), 48 of 50 passing on re-score, timing's estimated words
  down from 249 to 48. Final figures after re-recording the last two paragraphs: **all 50 paragraphs pass on Whisper's transcript of the audio, 43 of them word for word** (the rest
  one or two words: "his" for "the bridge's", a dropped "a"); 19.5 minutes; 25 of 3,270 words estimated in the
  timing. (`recorded.json` still lists three paragraphs as flagged: it was written by the run, before the
  normaliser learned Whisper's "10 .8" and "1st"; re-scored, they pass.).

## Decisions (change any)

- **GPT-Live on the library page has its own small client** (`page/library.js`, `overnight/live.py`). The
  live-voice switchover (worktree `hungry-germain-1ac517`) was uncommitted throughout, so nothing here imports it.
  When it merges, the two GPT-Live clients and the persona text (condensed from its instructions into
  `prompts/overnight_voice_guide.md`) should become one. The overnight recorder is separate by design (the
  switchover left overnight mode to this phase).
- **Every take is checked by Whisper**, not GPT-Live's transcript (the transcript said every cut-short take was
  fine). faster-whisper is now a declared dependency; a preflight checks it and its model before a build spends.
- **Migration copies, never moves**, and named the first chapters by the day their audio was made. Ian's live
  profile was empty (every `listener/` had been reset into `_cleared_*` folders); those are under
  `listeners/ian/archive/`, and nothing was made active from them.
- **Reset is "Start fresh"**: a new demo listener, made active; nothing moved, stopped or deleted.
- **Scheduler:** a launchd job runs `wl overnight tick` every quarter hour; tick plans at `plan_at` and builds at
  `start` on build days, following `settings.json` without reinstalling. A build may start up to 5 hours late (a
  laptop that slept), never in the afternoon. It holds off idle sleep while it runs (`caffeinate -i`). Waking the
  Mac before 2:00 is a system setting, left to Ian.
- **Retries:** a failed build is retried once, on the next build day; daytime `--now` tests don't use up
  tonight's try. A build process that died counts as failed. A failed planning pass is retried at most hourly,
  three times a window, then the card says so.
- **Cost:** the ledger and its update run through the Batches API (nobody waits). The runaway stop ($50) is the
  run's cap, checked before every Claude request and every GPT-Live take.
- **Chapter folders** are `<morning>-<slug>`. The research reuses the seed pipeline (scout, ledger, research,
  ledger update) with the card's angle in place of angles/choose; the outline comes after research; writing is
  fast start's paired edit with overnight prompts (no listener-waiting shortcuts).

## Review

A five-lens review (lifecycle, recording, live sessions, regressions, spec), each finding checked by two
skeptics: 29 confirmed, all fixed with regression tests. The ones that mattered: a Change near 02:00 could replace
a card the build had just taken and cause a second build (a cross-process card lock now); a retried build left
the card "failed"; a dead build left it "building" all day; a failed planning pass would have been retried and
paid for every 15 minutes; Ctrl-C left recording sessions running and billing; a tap while a session connected
left the guide talking over the chapter; migrated chapters kept their old run names; the latest chapter was picked
by folder name.

## Costs

`overnight/build_costs.jsonl`. Live: the probe $0.02, check 3 $0.30, check 5 $8.24 (research $3.26, writing
$3.54, recording $1.03, references and intro $0.36), the re-recording about $2. A nightly chapter: about $8-10.

## Open

- Checks 10-13 by Ian (`NEEDS_HUMAN.md`).
- The live-voice merge: one GPT-Live client and one persona.
- Asking during `playback.read_live` reading isn't built (a tap there stops the reading).
