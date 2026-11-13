# CLAUDE.md: context for agents working on write-listen

Read this first, then `docs/build-doc.md` (Ian's build doc, the spec),
`docs/ledger-research-addon.md` (Ian's add-on that replaces the research stage)
and `README.md` (how to run it). Ian does not build or run this himself: an agent
drives the pipeline **hands-off** (Ian, 2026-11-11). Model stages make the
decisions the build doc gave Ian at checkpoints; Ian judges only by listening.

## What this is

A Python CLI that turns one podcast transcript into one researched, edited,
narrated ~40-minute audio chapter, plus a single-prompt baseline for a blind
comparison. First run: *Can Beauty Make Us Good?* (The Atlantic, *The
Permanent Questions*, David Brooks with Elaine Scarry), run name
`beauty-scarry`. The question the MVP answers: does the staged pipeline make a
chapter clearly better than one prompt does? Ian judges by listening (five
success criteria, build doc § Goal).

## Status (2026-11-11)

| Milestone (build doc § Build order) | State |
| --- | --- |
| 1. Skeleton | Done. `ping` and `smoketest` pass live (local Mac, keys in `.env`). |
| 2. Voice test | Done. Ian picked Jane (`RILOU7YmBhvwJGDGjNmP`, ElevenLabs shared library, used straight from the library without adding it to the account); pronunciations approved. |
| 3. Ingest, map, angles | Done for `beauty-scarry`; `choose` (the editor) picked Angle 1, the ladder. |
| 4. Research, brief | Done for `beauty-scarry` (7 rows; 4 still partial after follow-ups). |
| 5. Pilot | Skipped: hands-off, so no pilot listening stop. |
| 6. Draft rest, assemble, edit, revise | Built, with the 07b coverage check before edit. |
| 7. Narrate, baseline, compare, report | Chapter narrated (42:51, $21.50 all in); Ian loved it. Baseline, compare and report not run yet (ElevenLabs characters). |
| Cost-pass test | `beauty-scarry-short` (20-minute run config): 19:44, $7.34 Claude (about $5.35 without a research pass redone after the fetch fix) + $1.72 ElevenLabs. Blind panel vs the first chapter: similar overall (7.3 vs 7.0); better to listen to (opening, ending, pauses), weaker on depth and classical accuracy (Alcibiades paraphrased: his Jowett lines never fetched). Script trimmed by hand by two asides to fit the ElevenLabs balance (untrimmed copy kept). |

The ledger add-on (branch `claude/script-research-focus-eqhyfe`) is merged
(fast-forward). After the first chapter, a cost pass (below) and three style
notes from Ian's listen: an opening that orients the listener, an ending that
stands alone (no next-chapter plug), and a few held beats (`[pause]`). Neither
has been tried on a live run yet. Offline tests pass (`.venv/bin/python -m pytest -q`): every
stage end to end against fake Claude and ElevenLabs clients (`tests/fakes.py`)
with real files and real ffmpeg.

**The hands-off run, in order:** `init --source <pdf>`, `map`, `angles`,
`choose`, `ledger`, `research --yes`, `ledger-update --yes`, `brief`, `draft`,
`assemble`, `coverage`, `edit`, `factcheck`, `revise`, `narrate`, `episode`; then `baseline`,
`narrate --baseline`, `compare`, `report`. `--yes` passes the $5 guard and
needs Ian's say-so for that run (he gave it for `beauty-scarry` on 2026-11-11).
Check the script's character count against the ElevenLabs balance before
narrating: one chapter is about 36,000 characters (a seed chapter about 18,000). The account
was on the `creator` plan, 121,074 characters a month, on 2026-11-12 (it was `starter`, 40,000).

## Phase 1: real-time onboarding (merged from `claude/realtime-onboarding`, 2026-11-12)

Spec: `docs/phase1-onboarding.md` (Ian, 2026-11-11). A listener talks with a
voice guide for a few minutes and, about half an hour later, hears a chapter
made from that conversation, with no human checkpoints. Milestones, in order;
the first is the gate for the rest:

| Milestone | State |
| --- | --- |
| 1. Seed to chapter, typed (`new --seed "..." --unattended`) | Done. Ian ran it locally (2026-11-12): "just as good as the article... tons of info, nice cadence". |
| 2. Status and page (`status.json`, FastAPI server, page) | Built, tested offline and in headless Chromium (idle, making, ready, problem; a typed topic starts a real run). Not yet used by Ian. |
| 3. The conversation in text (persona prompt, three tools) | Built, tested offline and in headless Chromium with a fake model. Not yet talked to live (no Anthropic key in the cloud). |
| 4. Voice (ElevenLabs Agents) | Built (2026-11-12, local): agent `write-listen guide` on Ian's account, page voice, voice-compare. Checked live: agent create/update, signed URL, text-only sessions from Python and from the page (client tool round trip). Not yet spoken to with a microphone (Ian's test). |
| 5. Memory (`listener/` files, profile update pass) | Built ahead of 4 (it needs no ElevenLabs account), tested offline with a fake model. |
| 6. End to end with a second person | Not started |

**Milestone 1 decisions.**
- A run with `00_source/seed.md` is a seed run (`common.is_seed`). Its stages
  load `prompts/<name>_seed.md` where one exists (`common.prompt_name`); the
  episode prompts are untouched, so an episode run renders exactly as before.
  Each variant's header names its original: keep the two in step. A test checks
  that no variant mentions the episode and that the research entry format
  (a parser contract) matches its original.
- `seed_runs` in config.yaml resizes a seed run (`common.run_config`, applied by
  the CLI to every stage): 3,000 words, 4 segments, 3-4 ledger rows.
- The scout replaces the map stage and writes `01_map.md`. Covered ledger rows
  mean "the scout's sources already settle it"; their verify agents check the
  claims the lesson would take from those sources. The JSON field stays
  `episode_has` (ledger.md labels it "Scout has").
- `--unattended` (`Run.unattended`, `Run.max_usd`): `llm.guard` approves against
  the run's cap instead of asking, `llm.call` checks the cap before every
  request, `llm.confirm` stops rather than ask, and the follow-up round trims to
  what fits (`ledger_update._fit_unattended`). `Run.decide` writes
  `decisions.md`. Starting an unattended run is the approval for spending up to
  its cap (default $20; a lesson's upper-end forecast is about $15).
- `--seed` is a string now; `report --seed N` still takes the sample seed.

**Milestone 3 decisions.**
- `stages/guide.py` `Session`: one conversation; each turn calls `models.guide`
  (Claude Sonnet 5.5, effort low) with `prompts/guide.md` and the tools in
  `prompts/guide_tools.json`, runs any tool and calls again (`llm.call` returns
  on `tool_use`). The same prompt and tools become the ElevenLabs agent's in
  milestone 4. The guide speaks first: the first user turn is a page note
  (`prompts/guide_opened.md`).
- `start_chapter` calls `server.Pipeline.start_seed` (the text box's path too);
  `add_note` appends to `00_source/notes.md`, which `common.seed_inputs` reads
  with the seed (so angles, choose, ledger and brief see it if they have not run;
  the guide is told when it is too late); `get_status` reads `status.json`.
- Names: a first meeting draws `guide.name_draw` (5) from `prompts/guide_names.md`.
  If `listener/guide.md` exists its text is the guide's identity (writing it is
  milestone 5).
- Transcripts: `listener/sessions/<id>.jsonl` as it happens; costs in
  `listener/sessions/log.jsonl`; `guide.max_session_usd` ($2) per conversation.
- The ideas doc the Phase 1 doc cites for the opening tone is not in the repo;
  the prompt follows the Phase 1 doc's own words (arrival, curiosity, short).

**Milestone 5 decisions** (built before 4 because voice needs Ian's ElevenLabs account).
- `stages/memory.py`: `after_session` (Opus, `prompts/memory.md`, structured
  output) rewrites `listener/profile.md` and `guide.md` from one transcript, once
  per session (`sessions/<id>.done`), skipping sessions where the listener said
  nothing. Triggers: a new conversation (the old one ended), `POST /api/chat/end`
  (the page's `pagehide` beacon), server start (`run_pending`), `cli.py chat` exit.
- `save_seed` copies each chapter's seed to `listener/seeds/<run>.md` when
  `start_chapter` runs; `record_chapter` appends to `history.md` when an
  unattended seed run finishes (code only).
- "Forget that" is honoured in profile.md and guide.md, and a forgotten chapter
  (`forget_chapters` in the pass's JSON) is removed from history.md and seeds/ by
  code; the raw transcripts in `sessions/` keep what was said (they are the
  record, like decisions.md).
- Passes are serialized (`memory.LOCK`) and ordered: a conversation older than
  the newest `.done` is marked skipped, not folded in. `server.new_session` runs
  the previous session's pass synchronously and joins background passes first.
  The module-level `server.app` does no catch-up at import; `python server.py` does.
- `POST /api/chat` and `/api/chat/end` need the session id (409 if it is not the
  current one); the page's beacon sends it. If the guide says nothing after a
  tool, a line from `prompts/guide_quiet.md` is spoken instead of silence.

**Merge with the cost pass and fact-check (2026-11-12, local).** The branch forked
before them. One `common.run_config(r, cfg)`: a seed run takes `seed_runs`, then
`runs/<run>/config.yaml` merges over that. Seed runs stream every stage
(`seed_runs.stages.*.batch: false`): a listener is waiting, and batching would add
minutes per stage. `new.py` runs 8b `factcheck` between edit and revise. The
style-guide rules (opening, standalone ending, `[pause]`) are shared; the seed
prompt variants got the same edits as their originals (claims about texts,
editorial brackets, the editor's wording checked, fetch rules, judge names sites).
`llm.call` on `tool_use`: a research loop with only server tools is written up
(Sonnet 5.5's dynamic filtering); a call with client tools (the guide) returns.
The episode entry in the dossier is for episode runs only. Checked live locally:
`server.py` serves the page and the typed guide answers; `cli.py chat` works.

**Milestone 4 decisions** (`stages/voice.py`, server `/api/voice/*`, the page).
- One persona: the agent's prompt is `prompts/guide.md` below its `---`, whose
  `{{placeholders}}` are ElevenLabs dynamic variables (same syntax), filled per
  conversation from `guide.Session.values`; nothing about the listener is stored
  on ElevenLabs. The three tools are ElevenLabs client tools (separate tool
  objects; `input_schema` -> `parameters`, no `additionalProperties`).
- `voice.sync_agent` creates or updates tools and agent so the repo stays the
  source of truth, keyed by a hash of prompt, tools and config (`listener/voice.json`).
  Config `voice`: `claude-sonnet-5-5`, effort `none` (Ian's pick; `llm.list` shows
  Sonnet 5.5 offers none..max, and Haiku 5.5 is listed too, despite the docs),
  `eleven_v4_turbo` (~100 ms; `eleven_v4` is not offered for agents), the
  narration voice, turn eagerness patient, 40-minute maximum.
- Private agent: `/api/voice/start` mints a signed URL (15 minutes, WebSocket).
  `first_message` is empty and the page sends `prompts/guide_opened.md` as a user
  message once connected, so the guide speaks first in its own words, as typed.
  Tools run server-side in the conversation's `guide.Session` (`/api/voice/tool`);
  lines go to the same transcript (`/api/voice/line`); ending runs the memory
  pass and saves ElevenLabs' call record (`save_conversation`). A chapter
  finishing mid-conversation is sent as a page note once the guide is listening
  (`prompts/guide_ready.md`; a contextual update would not make it speak).
  `sync_agent` saves ids as soon as each object exists and recreates only on a 404.
- Page: `@elevenlabs/client` 1.32.1 IIFE, pinned in config (`voice.client_js`,
  the newest release at least two weeks old) and injected when the page is
  served. Mic asked from the tap; any failure falls back to the typed chat.
- `voice-compare`: the same scripted turns to the agent (text-only, tools mocked)
  and the typed chat; sessions go to `listener/voice_compare/listener/`, the agent
  stays the listener's. Cost: a 6-message text-only conversation is about $0.06
  (LLM $0.04, mostly the first cache write; platform $0.003 a message).
- Effort check (2026-11-12): median time to first sentence on the platform was
  ~0.99 s at `none` and ~0.87 s at `low` (3 conversations each); a blind panel
  scored voice-none 8.0, voice-low 6.3, typed-low 6.0 (a lean). `none` stays.
  Reports in `listener/voice_compare/`; my test memory is in `listener/_tests_20261112/`.

**Milestone 2 decisions.**
- `stages/new.py` writes `runs/<run>/status.json` atomically before each stage
  and on ready or problem: `state` (making, ready, problem), `stage`, `label`
  (plain words), `stages_done`/`stages_total`, `minutes_left` (from
  `status_minutes`, rough until calibrated from `history`), `title`, `audio`,
  `message` (one plain sentence), `detail` (the error, for Ian).
- `server.py` (FastAPI, one file, 127.0.0.1:8765): the page, `POST /api/chapters`
  (seed -> `new <run> --unattended` as a subprocess), `GET /api/status`,
  `GET /api/events` (server-sent events), retry, audio. One run at a time. A
  process that dies while status.json still says making is shown as a problem.
- `page/index.html`: one file, plain JavaScript, system font, one accent color.
  Milestone 2 has the idle, making, ready and problem states; listening,
  thinking and speaking come with voice (milestone 4). The transcript toggle
  shows what was typed and the stages, until the conversation (milestone 3).

## Ask mid-chapter, by voice (branch `feature/ask-voice`, 2026-11-12)

Spec: `docs/ask-voice.md` (Ian). While a chapter plays, the circle reads "Ask a question"; a tap fades
the chapter out, the circle stirs (Connecting), the same guide answers by voice, and the chapter picks
up again. Built on the episode page work (merged into this branch, fast-forward): it needs
`11_episode/timing.json`. Chapter one has one (Whisper); every chapter narrated since narration kept
ElevenLabs' timings gets one from the unattended run's `episode` step (below). Report,
results, costs and Ian's test script: `ask/REPORT.md`. Open a finished chapter with
`http://127.0.0.1:8765/?chapter=<run>`.

- `ask/aside.py`: the chapter as the guide is told it (90 s just said and 3 minutes coming, word for
  word; the outline; dossier entries of the current and next segment in full, one line for the rest;
  the ledger; earlier asides), restart points (under 30 s the interrupted sentence, to 2 minutes one
  earlier, over that the paragraph; never back across a segment start), the log
  (`<run>/12_asks/asides.jsonl`, `seeds.md`). Paragraphs come from `episode.script` (`Sentence.para`).
- `ask/routes.py`: `/api/ask/*`, mounted by `server.py`; `/ask.js` with config `ask`'s page settings.
- A second ElevenLabs agent, `write-listen guide (aside)` (`stages/voice.py` `KINDS["aside"]`, ids in
  `listener/voice_aside.json`): `prompts/guide_aside.md` (keep its tone rules in step with
  `guide.md`), tools `resume_chapter` and `save_seed`, effort `low` (`ask.reasoning_effort`; at
  `none` it went back in the same turn as its answer, 7 of 14 scenarios). The guide agent's
  settings hash is unchanged.
- `resume_chapter` is called first, then the lead-in: the platform always returns a tool's result to
  the model, which speaks after it, so the result asks for the lead-in (`routes.RESUMED`) and the page
  ends the session once it has been said. With no reply expected, the agent called it in a loop.
- The page (`page/ask.js`) times the silences (`prompts/guide_aside_notes.md`) and the slow line /
  giving up; the agent's `turn_timeout` is 30 s so the platform never nudges first.
- `python -m ask.scenarios <run>` (checks 6-19, text-only, graded by Opus in a fresh context),
  `python -m ask.live <run>` (check 20, real page in headless Chromium).

**The timed transcript for new chapters** (episode/INTEGRATION.md sections 1 and 3, 2026-11-12):
- Narration asks ElevenLabs for timings (`narration.timestamps: true`): `tts.synthesize` calls
  `convert_with_timestamps` (same body, stitching and billing; audio whole as base64) and
  `narrate_segments` keeps `alignment` in each chunk's `.json`. Checked live on a 95-character
  script: the characters are exactly the text sent (pronunciations applied), every word matched.
- `new.py`'s `episode` step, after narrate: `timing.json` from those timings and `episode.json`
  without reference cards (Claude calls; `cli.py episode <run>` adds them, Whisper if a chunk lacks
  timings). Deliberate departures from INTEGRATION.md: the step never stops a chapter (it is finished
  either way), ElevenLabs' timings only (Whisper can take minutes and download a model while a
  listener waits), and its done-key is `11_episode/step.json`, written whether or not it built,
  so a finished run stays finished. Steps that cost nothing run even past the spending cap.

## Fast Start (branch `feature/fast-start`, 2026-11-13)

Spec: `docs/fast-start.md` (Ian). A second mode, `fast`: the opening plays about four minutes after the
topic is set, and the rest is researched, written, edited and narrated section by section while the
listener listens. `overnight` is unchanged. Report, live results, the fast-against-overnight comparison
and Ian's live-try script: `faststart/REPORT.md`. Code in `faststart/`, tests in `tests/faststart/`.

- `python cli.py new --seed "..." --unattended --mode fast` (cap `fast.max_run_usd`); a run with
  `fast.json` continues fast. The guide's `start_chapter` starts fast runs; the page's text box stays
  overnight. `python -m faststart e2e|overnight|report|notes-use|compare|spend|record` for live checks
  (`e2e` plays a simulated listener in real time: run it detached; `--narrate first|sim` saves
  ElevenLabs characters). Paid build steps go to `faststart/build_costs.jsonl`.
- The run: scout (`01_scout.md`, entries filed as row L00 = D01...), outline and one fresh-context edit
  with a code validator (`05_outline.json` is the truth; also `ledger.json`, `03_angle.md`,
  `05_brief.md`), the opening from the scout alone, research rows in parallel (30 searches for the
  outline's rows), then draft k with every earlier section's full text and lock k beside draft k+1
  (`06_segments/`), narration piece by piece with ElevenLabs timings (`10_audio/secNN/`), archive into
  the overnight layout so the episode page and ask-by-voice read it unchanged.
- `faststart/scheduler.py` is pure (no I/O, no clock); `faststart/run.py` drives it from one thread
  with lanes. Generation never waits for the listener; the only thing the listener changes is the
  solo lock after `fast.max_hold_seconds` of waiting. `status.json` stays `making` until archived (the
  server's invariants key on it); `first_audio`, `sections`, `waiting_for`, `check_in` are added.
- `04_dossier.md` is append-only: a D number never changes (unlike `research.build_dossier`).
- Listener notes: `listener_notes.md` (`faststart/notes.py`), not `00_source/notes.md`, for a fast run;
  read before every draft and lock.
- The page (`page/fast.js`, `faststart/routes.py`): markers, pieces through the one `<audio>`, position
  reports to `fast/player.json`, waits that pause or bring in the check-in, a third voice agent kind
  (`checkin`, `prompts/guide_checkin*.md`, state `listener/voice_checkin.json`; created on the account
  at the first check-in). Not yet tried with a microphone.
- `faststart/timings.json` (gitignored) holds the medians of this machine's runs: the only source of a
  time the guide may give.
- Prompts `prompts/fast_*.md` were tuned once after a blind comparison (commit `e4b37ac`); the
  diagnosis and what remains are in the report.

## Local setup

```bash
uv sync                      # Python 3.12+; pinned versions in uv.lock
brew install ffmpeg poppler  # macOS; on Linux: apt install ffmpeg poppler-utils
                             # (poppler fails to build on Ian's macOS 14; ingest uses pypdf then)
cp .env.example .env         # fill in ANTHROPIC_API_KEY and ELEVENLABS_API_KEY
```

`.env` is gitignored. The code reads `WRITE_LISTEN_ANTHROPIC_API_KEY` first,
then `ANTHROPIC_API_KEY` (from `.env`), and passes the key and
`https://api.anthropic.com` to the client explicitly. The dedicated variable
exists so the pipeline never uses or overrides a key that Claude Code itself
relies on. Without `ELEVENLABS_API_KEY` the ElevenLabs client sends a
placeholder (that only works in the cloud session, where a proxy injects the
real key), so locally it must be in `.env`.

## Decisions and deviations from the build doc

- **Hands-off (Ian, 2026-11-11).** No [Ian] checkpoints. `choose` (stage 3,
  `prompts/choose.md`) is the commissioning editor: it picks and tightens one
  angle from `02_angles.md` and writes `03_angle.md`, which the baseline also
  reads, so the comparison stays fair. The ledger, brief and script reviews are
  covered by the model stages that already judge them (04b, 07b, edit); a human
  may still edit those files, marked "(optional)" in `cli.py`. The pilot stop is
  skipped.
- **Ingest glyph repair** (`ingest.repair_glyphs`). The Atlantic's web font draws
  capital "Th" from private-use code points, so pypdf yields "ese" for "These";
  ff/ffi ligatures come out as single characters. Ligatures are written out;
  a private-use code point is restored as "Th" by evidence in the text itself
  (word position, spelled-out words), per use, and every guess, drop and
  mid-word restore is listed in `removed_lines.txt`. Chrome patterns match whole
  line shapes so dialogue is never cut. Two review rounds; regressions in
  `tests/test_ingest.py`.
- **Token caps.** Thinking counts against `max_tokens`: map needed ~40,000 and
  angles ~18,000 on this transcript, so stages writing long documents get 64,000
  (Opus 5.5 allows 128,000; billing is per token used).

- **Ledger-driven research replaced deep research** (Ian's add-on,
  `docs/ledger-research-addon.md`; it replaced the earlier planner, Sonnet
  topic agents and critic rounds). Stage 03b `ledger` writes 5-7 ranked rows
  to `ledger.json` (the source of truth; `ledger.md` is rendered from it).
  Stage 04 runs one agent per row (`models.researcher`, Sonnet 5.5 since the cost pass), budgeted by
  rank and type, under a 40-search cap for all agents that asks before going
  past. Stage 04b `ledger-update` judges each row, code checks every
  "filled", and open or partial rows get one follow-up. Stage 07b `coverage`
  checks the draft against the ledger for the editor. Built on the Messages API
  so every search result and fetched page stays in the run folder.
- **Budget enforcement.** `max_uses` resets on each request, so `llm.call`
  takes `resume_if`: a research agent's paused tool loop is resumed only if it
  has used none of its budget. Otherwise one call without tools
  (`prompts/research_writeup.md`) writes the findings from the pages it read,
  so no agent passes its budget and the 40-search cap holds. Not resuming also
  avoids an extra user message after a paused turn (which the API does not
  allow when the turn ends on a pending tool call). The tool loop's material is
  saved to `Lnn.pending.json` before the write-up, so a failed write-up is
  retried alone; `Lnn.md` and `Lnn.sources.json` change only on success.
- **Merge by code.** Entries are renumbered `D01`... with tags
  `(row(s), kind, tier N, verbatim: yes|no, verdict)`; exact duplicates across
  agents become one entry with both rows. No model rewrites an entry. Agents
  also return a `## Not found` section (not in the spec; 04b uses it for gaps).
- **Quote check.** Every `verbatim: yes` quote is matched word for word against
  the page it was fetched from, or the transcript for the episode, and
  downgraded to `verbatim: no` if absent, so the script cannot quote it. Tiers
  better than a known domain allows (`research.tier_domains`, e.g. Wikipedia is
  tier 4) are flagged in the source check, not changed. This serves success
  criterion 5.
- **Code checks on model judgments.** 04b's "filled" also needs an entry at the
  tier needed and a material entry (covered rows: a confirmed or corrected
  claim), else it becomes partial. For that check an entry counts at no better
  than its domain allows, and not at all if it cites only URLs no agent
  retrieved. 04b stops before any paid call if `04_dossier.md` no longer matches
  `04_research/dossier.sha256` (a hand edit its rebuild would lose), or if an
  active row has no research or changed in `ledger.json` since it was researched. 07b's quote report adds a mechanical check.
  The brief stage warns about rows assigned to no segment, and appends every
  corrected fact by code (`prompts/brief_corrections.md`).
- **Structured output** (`output_config.format`, a JSON schema in each stage
  module) for the ledger, ledger-update and coverage stages; checked live by
  the smoketest. Agents' findings stay line-formatted text, since they write
  them at the end of a web-tool loop.
- **Dossier markers.** Draft and revise tag every factual sentence `[D12]` or
  `[D03, D07]`. The editor checks them, narration strips them, the report
  samples them for the 10-claim spot-check.
- **Style guide precedence.** `prompts/style_guide.md` is the house style; the
  brief's own style section wins where they disagree (the brief is written for this chapter).
- **Refusal fallbacks are on** (`refusal_fallbacks: default`, server-side
  fallback beta). The log records which model served each call.
- **Cost.** `beauty-scarry` cost $17.74 of Claude ($8.74 of it research) and
  $3.76 of ElevenLabs. The cost pass (2026-11-11, Ian: "similar result at a
  lower cost") re-prices that run's tokens at $8.41 of Claude:
  - the researcher on Sonnet 5.5 (`models.researcher`), half the rate;
  - follow-ups only for rows ranked 1-3 (`research.followup_max_rank`): 4 of
    the 5 follow-ups left their rows partial anyway;
  - `batch: true` on single-shot stages (map, angles, choose, ledger,
    ledger_update, brief, coverage, edit): the Message Batches API, half price,
    slower, no refusal fallbacks (a refused or failed batch is re-sent
    streamed); draft and revise stream, being one call per segment in sequence.
    The batch id is saved to `runs/<run>/_batch_<label>.json` before the wait,
    so a run killed while waiting (a Bash timeout) resumes that batch rather
    than paying twice; network drops while waiting are ridden out, and if a
    batch's state can't be confirmed the stage stops (re-run to resume)
    rather than streaming a second copy. Quote checks treat editorial
    brackets ([T]he, [sic]) as the plain words. A batched stage can take
    minutes: run it detached;
  - per-stage `model:`; ledger_update and coverage (judges backed by code
    checks) on Sonnet 5.5. Drafting, editing and revising stay on Opus 5.5:
    they are the chapter's voice.
  Quality bar: the first chapter. Ian has $250 in Claude credits.
- **Fact-check pass, 8b** (`stages/factcheck.py`, Ian: fix the slips before
  pushing, 2026-11-12). On `beauty-scarry-short` a blind classicist found slips
  the editor missed. Four were in the editor's own replacement wording, which
  revise applies word for word and nothing checked afterwards; two came from a
  dossier tension entry (the researchers' reasoning) cited as fact. So a
  fact-checker in a fresh context checks every claim in the draft, marked or
  not, and the editor's proposed wording, against the dossier; its notes are
  merged under the editor's for revise and win where they conflict. The draft
  rules now treat claims about what a text says or leaves out as factual, and
  the editor is told its wording is checked. Batched, about $0.50 a chapter (it thinks at length: 45,000 output tokens on the short run).
- **Opening, ending, held beats** (Ian's listen, 2026-11-11). In the style
  guide (so the baseline gets them too), the brief, draft, edit and revise
  prompts. `eleven_v4` has no `<break>` tag and its bracket tags can be read
  aloud, so `[pause]` on its own line splits narration there and inserts
  `narration.beat_pause_ms` of silence. Editorial brackets in quotes ([T]he,
  [...], [sic]) are made speakable; any other bracket in spoken text stops
  narration before paying. The opening needs the show and the speakers, which
  no research row covers, so `research.build_dossier` appends one code-built
  `Source: episode` entry from the map's title and first paragraph, cited like
  any other. The build doc's criterion "wants chapter two" is read as "wants
  more" (`prompts/choose.md`): earned by the chapter, never by a plug.

## Rules (from Ian and the build doc)

- Run hands-off: no stops for Ian's judgment (an editor model decides); report
  at the end, with what was decided and what it cost.
- Stop and ask before any call or stage expected to cost more than $5. The code
  enforces this (`llm.guard`); `--yes` pre-approves. Do not pass `--yes` without
  Ian's say-so for that step.
- Keep it small: no frameworks or abstraction layers beyond the SDKs.
- The transcript is copyrighted. Never commit `runs/` (gitignored); quote the
  episode sparingly.
- Never put keys in code, logs, prompts or commits.
- Prompts live only in `prompts/*.md`, never inline in code. Inputs are listed
  above a `---` line, which `load_prompt` drops; `{{placeholders}}` below it
  must all be supplied, or it raises.
- Run the tests before every commit. Add a regression test with each bug fix.
- Ian dislikes invented distinctions and padding: be direct, recommend.

## Parser contracts (code depends on these formats; keep prompts and code in step)

| Where | Format | Parsed by |
| --- | --- | --- |
| angle (choose) | `# Angle: <title>` first line of `03_angle.md` | `choose.run`, the dossier title in `research` |
| ledger | `ledger.json`: `{"rows": [...], "parked": [...], "connections": [...]}`; row fields in `ledger.PLAN_FIELDS` and `LATER_FIELDS` | `ledger.validate`, `ledger.load` |
| row findings | inside `<research>`; entries `[L01.3] (kind, tier N, verbatim: yes) ... Source: URL` in `## Entries`; `## Not found` (prompts/research_format.md) | `research.extract_tagged`, `parse_entries` |
| dossier | `[D12] (L01, kind, tier N, ...) text. Source: URL` at line start inside `## Entries`; `Source: episode` for episode-only | `common.dossier_entries`, `entry_tags` |
| ledger, ledger update, coverage | JSON per the `SCHEMA` in `ledger.py`, `ledger_update.py`, `coverage.py` | `common.parse_json` |
| brief | `## Segment N: Title` with `Target words:`, `Ledger: L01, L03` and `Dossier: D03, D07` lines | `brief.parse_segments` |
| script | `<!-- segNN -->` before each segment; `[Dn]` markers after sentences; `[pause]` for a held beat | `common.split_script_segments`, `spoken_text`, `tts.split_beats` |
| edit notes, fact-check notes | `## Global`, `## seg01` (or `## Segment 1`) sections, in `08_edit_notes.md` and `08b_factcheck.md` | `revise.parse_notes`, `revise.merge_notes` |
| baseline | inside `<script>` tags | `baseline.run` |
| narration timings | `alignment` `{characters, character_start_times_seconds, character_end_times_seconds}` in `10_audio/chunks/<key>.json`, one character per character sent | written by `tts.narrate_segments`, read by `episode.timing.chunk_alignment` |
| timed transcript | `11_episode/timing.json`: segments > sentences (`segNN-sNNN` ids) > words with start/end | `episode.timing`, `ask.aside.Chapter`, the episode page |

## Layout

- `cli.py`: entry point; `python cli.py <stage> <run>`.
- `stages/common.py`: run folders, file guards (missing input stops; never
  overwrite without `--force`), `log.jsonl`, prompt loading, text helpers.
- `stages/llm.py`: the only Claude caller. Streams; logs tokens and cost per
  call; cost guard; retries dropped streams and overloads; resumes `pause_turn`.
- `stages/tts.py`: the only ElevenLabs caller (`synthesize`), chunking,
  pronunciation table, on-disk chunk cache, ffmpeg join.
- Seed runs: `new.py` (the unattended runner, writes status.json), `seed.py`, `scout.py`.
- `server.py` (the local server) and `page/index.html` (the page): Phase 1 milestones 2-3.
- `guide.py`: the guide's conversation (milestone 3); `memory.py`: the listener's memory (milestone 5).
- `ask/` and `page/ask.js`: asking mid-chapter by voice; `episode/`: the episode page (timed transcript, references).
- One module per stage; `ledger.py` (03b and the ledger data model),
  `research.py` (04 and the merge), `ledger_update.py` (04b), `coverage.py`
  (07b); `estimate.py`, `smoketest.py`, `voicetest.py`, `ping.py`.
- `config.yaml`: models, per-stage settings, research budgets, prices, narration.
- `docs/build-doc.md`: the spec; `docs/ledger-research-addon.md`: its research add-on;
  `docs/phase1-onboarding.md`: Phase 1, real-time onboarding.

## API facts that matter (checked against installed SDKs, anthropic 2.7, elevenlabs 2.77)

- Opus 5.5 and Sonnet 5.5: thinking can't be disabled (no `budget_tokens`, no
  `disabled`); control depth with `output_config.effort` (Opus 5.5 defaults to
  `medium`, so stages set it). No `temperature`. Forced `tool_choice` returns 400.
- Server web tools `web_search_20260209` / `web_fetch_20260209`. `max_uses` is
  per request. When a tool loop hits `pause_turn`, resend the turn with the
  tool list byte-identical: editing tools between continuations breaks the
  thinking-block check. A paused turn can end on a pending `server_tool_use`,
  so never append a user message after it. The research spend cap is checked
  before every request.
- Structured outputs (`output_config.format` with `type: json_schema`) work on
  Opus 5.5 with thinking and streaming; schemas may use `enum` and `anyOf` but
  not min/max/length constraints, and every object needs
  `additionalProperties: false`.
- Fetched pages come back as `web_fetch_tool_result` with
  `content.content.source.data` (text) or base64 for PDFs (extracted with
  pypdf). Still confirm with `smoketest`.
- ElevenLabs (live-confirmed): `eleven_v4`, 10,000 characters per request;
  responses carry a `request-id` header used for `previous_request_ids`
  stitching; `pcm_24000` works on the current plan (`pcm_44100` needs Pro).

## Cloud sessions

A personal Claude Code cloud environment `write-listen` exists, with the
ElevenLabs key saved as a network secret for `api.elevenlabs.io`. Anthropic
keys can't be added there (Anthropic hosts bypass the proxy), and elevenlabs.io's
docs site was not reachable from it, so Phase 1 development continues locally
(Ian, 2026-11-12).
