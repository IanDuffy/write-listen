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
| 4. Voice (ElevenLabs Agents) | Built (2026-11-12, local): agent `write-listen guide` on Ian's account, page voice, voice-compare. Ian spoke with it twice (2026-11-12): "fantastic so far", the chapter started from his spoken yes. His notes (too talkative, a natural hello, it kept talking while he waited, a low-cost way to wake it) are built on branch `voice/quiet-guide` (below), tested offline, in headless Chromium and as typed conversations; not yet spoken to (the ElevenLabs credits ran out). |
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
  Config `voice`: `claude-sonnet-5-5`, effort `none` at first (Ian's pick; now `low`, see
  below; `llm.list` shows Sonnet 5.5 offers none..max, and Haiku 5.5 is listed too),
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

**After Ian's first voice conversations (2026-11-12; branch `voice/quiet-guide`).**
- What went wrong, from the transcripts (`listener/_cleared_20261112*/sessions/`): the
  guide opened with a 60-word introduction and spoke 40-70 words a turn; and after "let me
  know when it's ready" it spoke 11 more times in 2.5 minutes, because ElevenLabs'
  `turn_timeout` (default 7 s) sends the agent an empty turn ("...") after each silence and
  it answered every one. A connection also bills about $0.08 a minute while silent.
- **Says little** (`prompts/guide.md`): a hello of two to five words and nothing else; no
  introduction (the name comes up in passing, at the latest when the chapter starts; what
  it makes, a few exchanges in); a turn is usually under fifteen words, one move a
  turn; never reasons aloud or talks about the listener as "they" (every word is spoken).
  `{{now}}` is the day and part of day. `{{sparks}}`: three subjects drawn from
  `prompts/guide_sparks.md` for the small delight it offers, as names are drawn: left
  alone it offered an octopus's hearts in 7 of 11 conversations.
- **The shape of a first conversation** (Ian, after his next test the same night; this
  replaces the Phase 1 doc's "share first, then ask" and "it decides"). After "Hi there"
  the guide had offered a fact about elevator buttons ("interesting but random") and
  explained plywood to a man building his own cabinets (a lecture); and his wife, asked
  cold what to talk about, felt put on the spot. Now, in order: **them first**: easy,
  concrete questions about their day, most turns only the next question, no facts and
  never an explanation of their own life or work, three questions or four at the most
  (fewer if they answer in a word or two); **then what it does**, in a sentence or two,
  **and the ask**: what would they like a chapter about, with edges (something from what
  they said, or something else, or the guide will pick) and never "anything"; if they
  hesitate it offers one or two ideas from what they told it, or to surprise them, and
  never asks twice; a choice they made or handed over is not confirmed again. **Small
  delights later**: once the chapter is under way and they want company (`{{sparks}}`).
  Tuned with simulated listeners talking to the typed guide (a builder, a reserved
  listener, a terse one, an eager one, a returning one; scratch script, Claude only) and
  a blind panel against the previous prompt: preferred in four of five pairs, and the
  panel's six edits (each checked by a skeptic) are in. What reads as lecturing, from the
  panel: a remark triggered by a noun rather than by what the person meant, about
  something they know better, that cannot be answered, before the guide knows them.
- **Effort `low`, not `none`** (`voice.reasoning_effort`). At `none` the model has nowhere
  to think: in 2 of 6 text-only test conversations it reasoned aloud ("They haven't
  picked, so I'll decide..."), which the voice would speak. At `low`: 0 of 3, shorter
  turns, and the earlier latency check had found no gain from `none`. This reverses
  Ian's pick; one line to put back.
- **Stepping back.** A fourth tool, `step_back` (when they'll wait, want quiet, or a turn is
  empty). By voice the page then ends the ElevenLabs connection and rests: no model, no
  cost. The server's `guide.Session` stays (the conversation is not over, so no memory pass
  yet). The page also rests on its own after `voice.rest_after_silence_secs` (25) without
  speech, and `turn_timeout` is -1 (accepted by the API; the docs give 1-30 s).
  The step_back call is answered only after the page has rested when the guide had
  already said its parting words (an answer has the model speak again: "Saying nothing
  more for now"); when the tool came before any words it is answered at once so it can
  say them (the tool's note asks). Typed, words then `step_back` end the turn.
- **Coming back.** While resting, `page/wake.js` (an AudioWorklet; also the pure
  `WakeDetector`, tested under Node) listens locally: voice-band energy at 4x the room's
  noise floor and above `min_rms`, 240 ms within 600 ms (`voice.wake`). A voice, a tap on
  the circle, typed text, or the chapter becoming ready calls `POST /api/voice/resume`:
  a new signed URL, the prompt's values with `{{earlier}}` (the transcript so far,
  `guide.earlier_text`, last 6,000 characters) and a fresh `chapter_now`, and a page note
  as the first turn for a tap (`prompts/guide_back.md`) or the chapter (`guide_ready.md`).
  A sound gets no note: it may not be the listener, so the guide only listens and the page
  plays two soft notes (speaking on every sound was the lingering voice again); it waits
  `rest_after_ready_secs` for words. Two sound wakes in a row with no listener turn, or
  `wake.max_sound_wakes` between taps, and the page stops listening for sounds (tap only).
  Not bounded: a television whose speech the platform transcribes as the listener's turns
  keeps a connection open, as with any open microphone; a tap-only mode or a wake word
  would be the fix, and is Ian's call. Each connection's ElevenLabs record is
  saved (`<id>.voice.json`, then `<id>.voice.2.json`...). A dropped connection is a rest,
  not an ending. "Stop listening", playing the chapter, closing the page, or
  `rest_max_minutes` (90, counted from the listener's last turn, tap or typed line) ends
  the conversation. Tapping the circle mid-conversation still ends it. If voice cannot
  come back (no credits, no network) the page says so under the circle and typed text
  goes to the typed chat.
- **Review** (five lenses, each finding checked by a skeptic): 18 confirmed, all fixed with
  tests. Besides the wake damper: a start in flight is abandoned when the conversation
  ends (pressing play while it reconnects), a chapter that becomes ready during a step
  back is still announced, the name the guide chose survives the 6,000-character cut,
  session ids are unique within a second, and the test fixtures no longer leave a memory
  thread that could reach the real API.
- **ElevenLabs credits.** A voice conversation costs about 480 credits a minute on the
  starter plan (tonight: 2,361 for 4.9 minutes), a text-only test conversation 75-370
  (971 when a mocked tool looped). The 40,000 a month ran out on 2026-11-12 (reset
  2026-12-10): Ian's two conversations used 3,639 and my 13 text-only test conversations
  4,181. Check `client.user.subscription.get()` before any live test; tune the persona
  with the typed chat (`cli.py chat`: same model, effort, prompt and tools, Claude only).
  Without credits the page falls back to typing ("Voice couldn't start"). Later the same night the
  account was on the `creator` plan (121,074 a month), with about 74,000 left on 2026-11-13: check
  the balance rather than trust either figure.

**Stop and reset (Ian, 2026-11-13).** `POST /api/chapters/{run}/stop` (`Pipeline.stop`):
SIGINT (new.py records the stop in decisions.md and the streaming call drops), then
SIGTERM and SIGKILL to the run's process group after 5 s and 3 s (research threads can
outlive Ctrl-C; a run started from a terminal is signalled alone, not the shell's group),
then any `_batch_*.json` batches are cancelled. status.json becomes `stopped` (pid None);
the page treats it as idle, the guide reads "stopped by the listener", retry still works.
`POST /api/reset`: drops the conversation without a memory pass (that would write it
back), joins passes in flight, stops a chapter being made (it would add itself to
history.md), and moves `MEMORY_FILES` to `listener/_cleared_<time>/` (moved, not deleted:
recoverable by hand; voice.json stays). The page asks before either.

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

**Merged with `voice/quiet-guide` (2026-11-13).** Where the two meet: the guide never rests (which
opens the local listener, `wake.js`) while a chapter plays or an aside is live, and waking refuses
during an aside; Reset memory also moves every chapter's aside transcripts and saved topics
(`runs/<run>/12_asks/`) into `listener/_cleared_<time>/asides/`, since the aside agent reads earlier
asides back; on a `?chapter=` page reset asks the server what is being made and keeps the chapter
shown; when voice can't come back after a rest the text box comes out (voice first, not voice
only); `tts.join_to_mp3` encodes to `chapter.part.mp3` and renames it, so a stop mid-encode never
leaves a short chapter that counts as narrated. Found by a three-lens review of the merge, each
finding checked by three skeptics; regression tests for each.

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
  server's invariants key on it); `first_audio`, `sections`, `waiting_for`, `check_in` are added, and for the page's progress
  (Ian, 2026-11-13) `steps`, `first_audio_in` and `step_ends_in` before the first audio, each section's `step`
  and the next one's `ready_in` after it: countdowns in seconds as of `updated`, which the page and the guide
  (`guide_hooks.plain`) run down themselves, in minutes rounded up. The countdown goes once `step_ends_in`
  runs out: the scout and the outline write no status while they run.
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

## One live voice: GPT-Live (branch `claude/hungry-germain-1ac517`, 2026-11-13/14)

Spec: "One Live Voice (GPT-Live replaces ElevenLabs)" (Ian, 2026-11-13; not in the repo, summarized in
`voice/gpt_live/REPORT.md`). The guide's conversation and the fast-start chapter's reading run in **one
OpenAI GPT-Live session**, in one voice (Gleam). Ian: onboarding first; the overnight chapter work is a
later phase. Report, doc answers, live results, costs and Ian's script: `voice/gpt_live/REPORT.md`.

- `config.yaml live:`: `provider: gpt_live` serves `page/live.html` at `/` (`elevenlabs` puts the old page
  and agents back; ElevenLabs code is left in place, unused, until Ian has tried this). `?chapter=<run>`
  still opens the old page. Models: voice `gpt-live-1`, backend `gpt-6.1-terra` (Terra), voice `gleam`.
- **GPT-Live is not the Realtime API** (checked live): full duplex, it decides when to speak; no
  `response.create` for speech, no "response done"/"audio played" events, no cancel/truncate/item delete,
  no `opening` session field (refused). Text goes in as appended context (500 tokens each): the page
  reads paragraphs as `session.commentary.append` ("Read this passage aloud now, word for word...");
  notes as `session.instructions.append`, which GPT-Live **ignores while the mic is muted**, so the page
  unmutes first. The guide's tools run through **delegation** to a Responses backend (Terra): the voice
  says "Generate <|SpawnThinking|>", the backend calls a function, the page runs it at `/api/live/tool`
  and answers `response.item.create` (function_call_output) + `response.create`.
- `voice/gpt_live/`: `session.py` (instructions, tools, session config, WebRTC SDP exchange: no client
  keys exist for GPT-Live, so the page's offer goes through our server), `chapter.py` (locked sections
  as paragraphs `s<n>-p<kk>`, restart points, the question's context, the reading check),
  `routes.py` (`/api/live/*`, `/live.js`, `/align.js`), `instructions/` (the guide's instructions, one
  file per part, assembled in name order; `backend.md` is the backend's; Ian edits them, notes above
  `---` as in prompts/), `notes.md` (the page's notes to the guide), `pagecheck.py` (live checks of the
  real page in headless Chromium with a TTS-spoken listener), `sim.py`/`persona.py` (WebSocket harness,
  simulated listeners, Opus grading), `archive.py` (the session recording to the episode).
  The instruction files live outside prompts/ because the spec asks for `voice/gpt_live/instructions/`.
- `page/live.js` follows the **output transcript** word by word against the paragraph being read
  (`page/align.js`: deltas can split a word); the next paragraph goes once the current one is heard to
  its end (sent earlier, GPT-Live dropped it or cut a sentence). A tap ducks the audio, opens the mic and
  appends a stop note plus silent context; `resume_chapter` (or the page, after a silence) re-feeds from
  the restart sentence. A check-in when the next part isn't written runs in the same session.
  A new session takes over at a section boundary near GPT-Live's context or 2-hour limit.
- Fast runs started from the page are **live runs** (`fast.json` `"voice": "live"`, `faststart/live.py`):
  no narration step, "ready" means locked, status pieces are paragraphs, the archive has no audio until
  `voice/gpt_live/archive.py` builds it from the session recording (`store: true`, kept 30 days by OpenAI).
- **Whose chapter** (after Ian's first test, 2026-11-14: an older finished chapter, the newest run, was read
  the moment he spoke). The page reads only `L.own`: the run its `start_chapter` returned (the tool route
  adds `run`, the page strips it before the backend sees it), or the one `/api/live/start` returns as
  `continue` (`routes.continuing`: still making, or its reading stopped under `live.continue_minutes`
  ago and not archived; opened with notes `reloaded`/`reopened`). Older runs are neither read nor shown,
  and live `get_status` (`guide.Session(own_only=True)`) doesn't report them. The lead-in waits for the
  session's first line, an answer to the guide's question and a pause; a promised start with no
  `start_chapter` gets one reminder (`start_promise`, `start_nudge`). The page greets with speakable context
  at the session's start (no startup message: `session_config(greet=False)`); `live.opening` sets first
  words (no API field exists for it). Pagecheck worlds have no status until `adopt()`.
- **After Ian's second test** (2026-11-15; report section of that name). The meeting unfolds a turn at a
  time (`opened`: a hello alone; names next; an offer to say what this is), warmer and less of an interview
  (laughs, guesses, small pieces of itself); the page nudges toward a pitch at `pitch_nudge_secs` and checks
  in once on an unanswered hello. A ready chapter is **offered**, never begun mid-talk: it starts on the
  `start_reading` tool (`prompts/live_tools.json`), the listener's yes to an offer question, the guide's
  lead-in, a tap or the play button; resting, it is led into as before (`first_ready_waiting`). Delegation
  misses tools, so the page also reads lines: quiet asked for + the guide's parting words rest the page
  (mic off) without `step_back`. Play/pause and a slider over the script at `words_per_second` (pause and a
  move stop the guide with `paused`/`moved`; a long pause closes the session, play opens one with reason
  `paused`); the end holds the session `end_hold_secs`. A tap's reading tail isn't an answer, and a tap
  nobody speaks after becomes a pause. The ring fills from the run's start. `routes.tool` drops
  `while_waiting` (the older guides' wait rules). Tests turn the clock cues on through `Live.S`.
- **After Ian's third test** (2026-11-15; report section of that name; his run saved in
  `listener/_saved_20261115-132151/`). Leave-taking ("we'll get back to you") rests the page too; resting has
  wake on sound again (`page/wake.js` on a clone of the mic track; `wake_listen_secs`, `voice.wake`). The ready
  chapter is said word for word through `opening_words` (`offer_line` in talk; `ready_line_resting` with the mic
  off, where the play button shows); no play button in talk. Pause closes the GPT-Live session (`closeLive`);
  play reconnects with reason `paused`, a tap while paused with `paused-ask` (`asking`). In a question, stalls
  and a bare "okay" aren't answers (`live.page.stall`, anchored), `unanswered` asks it to answer, the backend may
  answer a handed-over question, and a quiet aside ends (resumed if answered, else paused).
- **After Ian's fourth test** (2026-11-15). The "what I do" offer is `{{offer}}` in 20_meeting.md:
  `offer_first.md` until the listener has been told (a returning listener, or `session.explained()` on resume),
  then `offer_known.md`. An outright "turn off your mic" (`mic_off_ask`) rests tap-only; other rests wait
  `wake_grace_secs` before listening, with `live.page.wake` over `voice.wake`.
- Costs: GPT-Live $0.05 per connected minute (muted and silent time too) + backend tokens, logged per
  session in `listener/sessions/log.jsonl`; build checks in `voice/gpt_live/build_costs.jsonl` (cap $20).
- Tests: `tests/live/`; `tests/conftest.py`'s `cfg` pins `live.provider: elevenlabs` for the older tests.

## Overnight Chapters (branch `feature/overnight`, 2026-11-15)

Spec: `docs/overnight-chapters.md` (Ian). Every weeknight the app builds the listener's next 20-minute chapter;
an Up next card shows tonight's plan, which the listener can change by voice until 2:00; in the morning Play
opens a short live intro by the guide, then the recording. Report, live results and costs:
`overnight/REPORT.md`; contracts: `overnight/DESIGN.md`; checks for Ian: `NEEDS_HUMAN.md`. Code in `overnight/`.

- **Listener profiles.** Everything for a listener lives in `listeners/<id>/` (gitignored): `profile.md`,
  `guide.md`, `settings.json` (over config.yaml `listener_settings`), `library.jsonl`, `seeds.jsonl`,
  `up_next.json` (the card), `queue.jsonl`, `plans/`, `sessions/`, `chapters/<date>-<slug>/`, `archive/`.
  `listeners/active.json` names the active one. One resolver: `stages/common.listener_dir(cfg)` and
  `chapters_dir` (a `listener_dir` in cfg still wins: tests, e2e); `Run.open` with no folder looks in the active
  listener's chapters; a run in `<listener>/chapters/` is pinned to that listener by `run_config`, whoever is
  active. Voice agent ids moved to `shared/voice/`. **Start fresh** (the page's old Reset memory, `POST
  /api/reset`, `wl listener reset`) makes `demo-<date>-<letter>` active and touches nothing else.
  `wl listener migrate` copies the old `runs/` and `listener/` in (never moves).
- **The build:** `wl overnight run [--now]` (a script `wl` at the root runs `cli.py` with the venv). Stages, each
  resumable by its file: lock (card into `00_source/up_next.json`, `seed.md` from it) → research (the seed
  pipeline's scout, ledger, research, ledger update; the card's angle replaces angles/choose) → write (outline
  from the full dossier, then fast start's paired edit through `faststart.sections` with overnight prompts) →
  promise (check against the card's summary, one revision) → record (GPT-Live server-side, `overnight/record.py`)
  → assemble (Whisper timing, references, `episode.json`) → intro brief → finish (library, seeds, history, card
  ready). `status.json`, `cost.json`; the runaway stop is `settings.json` `overnight.runaway_stop_usd` as the run's cap.
- **Recording facts (live).** No `delegation` needed server-side; mic muted, one passage outstanding, sent as
  commentary. **Never cut takes by the transcript's `start_ms`**: its clock runs seconds behind the audio over a
  session (34 of 50 paragraphs lost their openings). A take is everything audible since the last, trimmed. Each
  take is checked by local Whisper (`overnight.record.check`), GPT-Live's transcript as a tie-breaker for names;
  numbers in digits normalised; a passage it always stops short gets a pickup of the missing sentences.
- **Scheduling:** `wl overnight tick`, every quarter hour: `wl overnight wait` in a terminal (macOS refuses a
  launchd job access to `~/Desktop`, where the repo lives; `wl overnight schedule install` is the launchd entry for
  a repo elsewhere, or with Full Disk Access granted),
  plans at `plan_at`, builds at `start` on build days (`overnight/schedule.py` is pure). A card lock
  (`overnight/locks.py`) keeps the tick and the server's Change from crossing; a dead build counts as failed
  (`runner.reconcile`); a failed card is retried once on the next build day; failed planning passes back off.
- **The library** (`page/library.js`, mounted below the orb on `/` (`page/live.html`, and `page/index.html`
  for the ElevenLabs provider); `/library` is a standalone shell): Up next (Change, Add a note), the latest chapter,
  every past chapter, a chapter view at `#/chapter/<run>` and one mini player whose audio survives navigation.
  Its GPT-Live sessions (intro, change, ask on a recording) are `overnight/live.py` with prompts
  `prompts/overnight_voice_*.md`: a second, smaller GPT-Live client beside the live page's; they never run at
  once (`window.Orb` / `window.Library` busy/end). Merging the two clients and personas is open.

## Library Timeline and Listening Notes (branch `feature/library-timeline`, 2026-11-16)

Spec: `docs/library-timeline.md` (Ian). Progress, the gaps found and decisions: `PROGRESS.md`; checks 9, 12, 13:
`NEEDS_HUMAN.md`. The library (page/library.js) is now two screens: the chapter strip with a scrubber of real dates
and "From this chapter" (sketch B), and the chapter page `#/chapter/<run>` (sketch D: progress bar, a marker per
moment, every moment with its outcome); the read-along moved to `#/chapter/<run>/transcript`. Only the latest chapter
plays. The library's Play is `#lib-play` (the orb page has its own `#play`).

- `overnight/timeline.py`: `chapters/<id>/moments.jsonl` (rewritten whole under a lock file), `playback.json`
  (`POST /api/chapters/<run>/playback`: the player reports at a play's start, every 10 s, on pause and at the end),
  and `state()`, `/api/library`'s `timeline`. `became_chapter`'s `outcome_ref` is the card's plan id, resolved at read.
- Capture: an ask on a recording now gets the after-conversation pass (`overnight.live.memory_on_asks`), whose
  `moments` (asks only) `memory.remember_moments` writes at the tap position from the session's first line;
  `ask/aside.save_seed` writes a note moment; `planner.moments_follow` marks the moment a card's seed came from and
  puts it back to Saved (and the seed back in the pool, `library.mark_unused`) when the card is replaced unbuilt.
- Demo: `wl demo seed [--use]` rebuilds `listeners/demo-library/` from `demo/library_demo.json` (filler cards from
  the real planner; the Beauty and Herons chapters copied from `listeners/ian`, read only). The tick skips a listener
  whose settings.json has `demo`.
- Visual tokens: the app's own (system font, `--accent` orange, dark mode) where it sets one, per the spec's rule.

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
- Prompts live only in `prompts/*.md` (the live guide's in `voice/gpt_live/instructions/`, per its spec), never inline in code. Inputs are listed
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
- `voice.py`: the ElevenLabs agent (milestone 4); `page/wake.js`: the page's local listener while the guide rests.
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
