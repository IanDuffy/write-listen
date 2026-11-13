# write-listen: commissioned audio chapter MVP

A small local pipeline that turns one podcast transcript into one researched,
edited, narrated 40-minute audio chapter, plus a single-prompt baseline to
compare it against in a blind listen. The spec is the build doc,
[`docs/build-doc.md`](docs/build-doc.md), with its add-on for ledger-driven
research, [`docs/ledger-research-addon.md`](docs/ledger-research-addon.md).
Agents: start with [`CLAUDE.md`](CLAUDE.md) for status, decisions and next steps.

Each stage reads files from a run folder and writes one new file, so any
output can be read and edited by hand before the next stage runs. A stage
refuses to run if its input is missing, and never overwrites an output
without `--force`.

## Setup

```bash
uv sync                       # creates .venv with the pinned versions in uv.lock
```

## API keys

Two keys: an Anthropic API key (console.anthropic.com, Settings, API Keys,
in the organization that holds the credits) and an ElevenLabs API key
(elevenlabs.io, profile, API Keys, with text to speech and read access to
voices, models and user).

**In a Claude Code cloud session** (the `write-listen` environment):

- ElevenLabs: saved as a network secret for `api.elevenlabs.io`. The session's
  proxy adds it to every request, so no variable is needed.
- Anthropic: an environment variable named `WRITE_LISTEN_ANTHROPIC_API_KEY`,
  added in the environment's settings (the cloud environment menu in the
  session's title bar, then Edit, then environment variables). Requests to
  Anthropic don't pass through the proxy, so a network secret can't be used.
  The pipeline reads its own variable name so that it never touches the key
  Claude Code itself uses. The variable is picked up when a session starts.

**On your own machine:** `cp .env.example .env` and fill in
`ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY`. `.env` is gitignored.

Needs Python 3.12+, `ffmpeg`, and ideally `pdftotext` (poppler); `pypdf` is the
fallback for PDF text. `.env` and everything under `runs/` are gitignored: the
transcript is copyrighted, and every output derives from it.

## Milestones

Each milestone stops for Ian.

1. **Skeleton.** `python cli.py ping` makes one tiny Haiku call and checks the
   ElevenLabs key (it prints the plan and character quota). Both are logged to
   `runs/ping/log.jsonl`. Then `python cli.py smoketest` (about $0.10) checks
   every API assumption the pipeline rests on: that search and fetch results
   come back in the shape we parse, that fetched page text is captured for the
   quote check, that the narration model and voice exist, and that responses
   carry the request id that stitching needs. It prints PASS or FAIL for each.
2. **Voice test.** `python cli.py voices` lists the models and voices on the
   account. `python cli.py voicetest` narrates `voice_test/sample.md` (about
   300 words on Diotima, naming Weil, Boethius and Aquinas) in each voice under
   `narration.candidate_voices`, or `--voices ID1,ID2,ID3`. Pick one and set
   `narration.voice_id` in `config.yaml`. Fix any names in
   `narration.pronunciations`.
3. **Ingest, map, angles.**
   ```bash
   python cli.py init beauty-scarry --source ~/Downloads/transcript.pdf
   python cli.py map beauty-scarry
   python cli.py angles beauty-scarry
   ```
   Check `00_source/removed_lines.txt` to see what ingest stripped. Then write
   `runs/beauty-scarry/03_angle.md` by hand.
4. **Ledger, research and brief.**
   ```bash
   python cli.py ledger beauty-scarry               # 03b: ledger.json + ledger.md
   ```
   Stop: Ian reviews `ledger.md` and edits `ledger.json` (add, cut, re-rank,
   park or rewrite rows); `python cli.py ledger beauty-scarry` again re-checks
   the file and re-renders `ledger.md`. Then:
   ```bash
   python cli.py research beauty-scarry --plan-only   # budgets and estimate, nothing spent
   python cli.py research beauty-scarry --rows L01    # the rank-1 row first (L01 unless re-ranked): show Ian 04_research/L01.md
   python cli.py research beauty-scarry               # the other rows, then 04_dossier.md
   python cli.py ledger-update beauty-scarry          # 04b: judge each row, one follow-up round
   python cli.py brief beauty-scarry
   ```
   Then edit `05_brief.md` by hand. Keep its `## Segment N: Title` headings and
   its `Target words:`, `Ledger:` and `Dossier:` lines, because the draft and
   coverage stages read them.
5. **Pilot.** `draft beauty-scarry --segments 1-2`, then
   `narrate beauty-scarry --pilot` gives `10_audio/pilot.mp3`. Adjust
   `prompts/style_guide.md` and the prompts from the listen.
6. **Draft the rest, assemble, coverage, edit, fact-check, revise.** `draft` keeps the
   segments that already exist, so this drafts only the rest. Then run
   `assemble`, `coverage` (07b: the draft against the ledger), `edit` (which
   reads `07b_coverage.md`), `factcheck` (8b: every claim in the draft and in the editor's
   proposed wording, checked against the dossier) and `revise` (which applies both sets of notes), and read `09_script_final.md`.
7. **Narrate, baseline, compare, report.**
   `narrate`, `baseline`, `narrate --baseline`, `compare`, `report`.

`python cli.py status <run>` shows what exists, what's next, and spend so far.
`python cli.py estimate [run]` forecasts the cost of the rest of a run.

## Seed runs: a lesson from a listener's topic (Phase 1)

Phase 1 ([`docs/phase1-onboarding.md`](docs/phase1-onboarding.md)) ends in a
voice conversation that starts a chapter. Its first milestone, built here, is
the pipeline half: a seed in, a finished 20-minute lesson out, with nobody
approving anything.

```bash
python cli.py new --seed "I can't stop thinking about why Roman concrete lasted" \
                  --why "I pour concrete for a living" --unattended
```

- **Seed.** `00_source/seed.md` holds the topic in the listener's words, why it
  matters to them, and anything to include or avoid. `--seed-file` takes one
  written elsewhere (the guide's conversation, later). The run is named from
  the seed (`roman-concrete-lasted-20261112`) unless you give a name.
- **Scout** (stage 1, Opus with 10 searches and 5 fetches) maps the territory
  around the seed and finds candidate sources. It writes `01_map.md`, so the
  stages that read the episode map read the scout's map instead.
- **Angle.** `angles` proposes 3-4 angles; `choose` picks one using the seed
  and the listener's profile (`listener/profile.md`, if it exists) and says why.
- **The rest is the chapter pipeline,** sized as a lesson by `seed_runs` in
  `config.yaml`: about 3,000 words, 4 segments, 3-4 ledger rows. A seed run's
  stages load `prompts/<name>_seed.md` where one exists (12 of them; same
  inputs and output contracts, with no episode and an opening that ties back
  to what the listener said). Episode runs are unchanged.
- **Unattended.** Nobody is asked anything. Before spending, the upper-end
  forecast for the rest of the run is checked against the run's cap
  (`unattended.max_run_usd`, $20, or `--max-usd`). Each stage's cost guard then
  approves against that cap, and every request checks it, so the run stops and
  reports rather than pass it. The follow-up round runs only what fits the
  40-search cap and the money cap. Every decision a person used to make goes to
  `decisions.md` (the angle and why, the ledger, follow-ups left out, the brief,
  going past the $5 guard, a stop), for Ian; the listener never sees it.
- **Continue.** A stage whose output exists is skipped, so after a stop the
  same command continues from there: `new --seed "..."` finds the unfinished run
  with that exact seed, and `python cli.py new <run> --unattended` names it. A
  scout or research write-up that failed is retried from its saved material.
  Without `--unattended`, `new` only writes the seed, and the stages run one by
  one as before, starting with `python cli.py scout <run>`.

`listener/` (the profile, later the guide's memory) is gitignored.

**The page (milestone 2).** `.venv/bin/python server.py`, then open
<http://127.0.0.1:8765>. Type a topic and press Make it: the server starts
`new <run> --unattended` in its own process (its console output goes to
`runs/<run>/run.log`) and streams the run's `status.json` to the page. The
circle grows a progress ring with the stage in plain words and the minutes
left ("Researching · about 12 min"); when the chapter is ready it becomes a
play control under the chapter's title, and the browser sends a notification.
If a run stops, the page says so in one sentence and offers Try again, which
continues the run. The server listens on 127.0.0.1 only, and runs one chapter
at a time. The minutes come from `status_minutes` in `config.yaml`, first
guesses; each `status.json` records every stage's real duration (`history`)
to calibrate them.

**The guide (milestone 3, typed).** Tap the circle and the guide speaks first;
the text box talks to it. Its persona is `prompts/guide.md`: after a bare hello
it gets to know the listener with easy questions and very little of its own (no
facts, and never an explanation of their own work); after three or four it says
in a sentence or two what it does and asks what they would like a chapter
about, offering to pick if nothing comes to mind; small delights wait until the
chapter is under way. Its tools are
`prompts/guide_tools.json`: `start_chapter` starts the unattended run,
`add_note` passes on something said afterwards (stages still to come read
`00_source/notes.md` with the seed), `get_status` reads `status.json`. A first
meeting gets five names drawn from `prompts/guide_names.md` to choose from. It
runs on Claude Sonnet 5.5 (`models.guide`), the fastest Claude that ElevenLabs
Agents offers natively (`docs/elevenlabs-agents-check.md`), so voice will use the
same model. Each conversation is saved to `listener/sessions/<id>.jsonl`, its
cost to `listener/sessions/log.jsonl`; one stops talking past
`guide.max_session_usd` ($2). `python cli.py chat` is the same conversation in
the terminal, the cheapest way to tune its tone.

**Voice (milestone 4).** Tapping the circle starts a voice conversation with the
same guide: an ElevenLabs agent whose prompt is `prompts/guide.md` and whose
client tools are `prompts/guide_tools.json`, on Claude Sonnet 5.5 at reasoning
effort `low`, speaking with the chapters' voice on `eleven_v4_turbo`.
`python cli.py voice-agent` creates it (the server also re-syncs it whenever the
prompt, tools or `voice` settings change; its id is in `listener/voice.json`).
The agent is private: the server mints a signed URL per conversation, the page
runs the tools by calling the server, and the transcript and memory work as in
the typed chat; ElevenLabs' record of each call (per-turn latency, cost) is saved
to `listener/sessions/<id>.voice.json`. Without a microphone or voice the page
falls back to typing. `python cli.py voice-compare` sends the same scripted
turns (`prompts/voice_compare_turns.md`) to the voice agent, text-only with its
tools mocked, and to the typed chat, and writes them side by side to
`listener/voice_compare/`.

**Stepping back and coming back.** The guide says little (a bare hello, one
move a turn) and never talks into a wait. When the listener says they will wait,
or nobody has spoken for `voice.rest_after_silence_secs`, it steps back (its
`step_back` tool, or the page on its own): the page ends the voice connection,
so no model runs and nothing is billed, and rests. While it rests,
`page/wake.js` listens on the laptop only (a sound in the voice band, well above
the room, for a quarter of a second) and nothing it hears leaves the page.
Speaking, tapping the circle or typing brings the guide back into the same
conversation, and so does the chapter being ready, which it says in a sentence.
Coming back is a new connection (`POST /api/voice/resume`): the agent is told
the conversation so far with its prompt. A sound may not be the listener, so
when one wakes it the guide says nothing: the page plays two soft notes and it
listens (it did not hear the words that woke it; say them again). After two
such wakes in a row that nobody follows up (a television, music), only a tap
brings it back. "Stop listening" under the circle, or `voice.rest_max_minutes`
of rest, ends the conversation and releases the microphone.

**Stop and reset.** While a chapter is being made, "Stop making this chapter"
(it asks first) ends it at once: Ctrl-C to its process, so it records where it
stopped and its Claude call is dropped, then terminate and kill if it lingers,
with anything it started; queued batch requests are cancelled. What it made is
kept (`python cli.py new <run> --unattended` continues it). "Reset memory" in
the footer (it asks first) makes the guide forget the listener: the
conversation ends without a memory pass, a chapter being made is stopped, and
`profile.md`, `guide.md`, `history.md`, `seeds/` and `sessions/` move to
`listener/_cleared_<time>/`, which nothing reads (delete it to be rid of it).
Finished chapters stay, and so do ElevenLabs' own records of voice calls.

**Memory (milestone 5).** Plain files in `listener/` (gitignored), readable and
hand-editable: `profile.md` (what the listener has said about themselves, under a
page), `guide.md` (the guide's name, how it chose it, when they met),
`sessions/<id>.jsonl` (each conversation), `seeds/<run>.md` (each chapter's seed,
saved by `start_chapter`) and `history.md` (each finished chapter: title, angle,
parked ideas). When a conversation ends (the page closes, a new one starts, or
`cli.py chat` exits), Opus rewrites `profile.md` and `guide.md` from its
transcript (`prompts/memory.md`): only what the listener said, merged rather than
appended, and anything they asked to forget removed (a forgotten chapter also
leaves `history.md` and `seeds/`). Passes run one at a time and in order: a new
conversation waits for the last one's pass, and a conversation older than one
already remembered is skipped, so an old pass can never bring back something
forgotten. A server that stopped first catches up at its next start. The guide
reads all of it at the start of the next conversation, so a returning listener
is greeted as one.

## Ledger-driven research

Research is aimed by the chosen angle, not by general reading. The spec is
[`docs/ledger-research-addon.md`](docs/ledger-research-addon.md).

1. **Ledger (03b, Opus).** From the full transcript, the map and the angle,
   Opus writes the 5 to 7 ideas the chapter must land, ranked by importance to
   the angle, to `ledger.json`. Each row says what the episode already has, and
   is `deep-dive` (the episode skims it), `missing` (the episode lacks it) or
   `covered` (the episode gives enough; only its facts get checked). It also
   gives one narrow research question and the source tier needed to settle it.
   Ideas that miss the cut go to a `parked` list and are never researched.
   `ledger.json` is the source of truth; `ledger.md` is a table rendered from
   it by every stage that touches it. Ian edits the JSON. Row ids follow rank,
   so `ledger --force` (a new ledger) moves any old `04_research/` aside.
2. **One agent per row (04, Sonnet 5.5 with web search and fetch; `models.researcher`).** Budgets
   by rank and type: ranks 1-3 get 8 searches and 4 fetches, lower ranks 4 and
   2, covered rows a verify-only agent with 2 and 1. All research agents
   together, follow-ups included, stay under 40 searches: the stage asks before
   a run whose budgets would go past that. Each agent sees its row, the angle,
   the map, what the episode already says, and the other rows' ideas. It
   returns numbered entries `[L01.3]` tagged by kind: claims, tellable material
   (a scene, story or image), a primary quote, the strongest tension, and
   connections to other rows. Each entry gives its source tier. Verify agents
   mark each episode claim `confirmed`, `corrected` or `unverified`. Up to 4
   agents run at a time. Each writes `04_research/L01.md` and
   `L01.sources.json` (every URL it saw, the text of every page it fetched).
   The API applies the tools' `max_uses` per request, so a resumed tool loop
   would get its whole budget again. An agent whose loop pauses after using
   any of its budget is therefore not resumed: one call without tools writes
   its findings from what it already read. No agent can pass its budget.
3. **Merge (code).** Once every active row has findings, code renumbers the
   entries `D01`, `D02`, ... into `04_dossier.md`, in rank order, keeping each
   entry's row, kind and tier. Exact duplicates across agents become one entry
   tagged with both rows. No model rewrites an entry.
4. **Ledger update (04b, Sonnet 5.5, batched).** For each row: `filled` (answered at the tier
   needed, with tellable material), `partial` or `open`, the dossier entries
   that serve it, and the best tier found. Code checks every `filled` against
   the dossier itself. Each open or partial row ranked 1-3 (`research.followup_max_rank`) gets one follow-up agent (4
   searches, 2 fetches) with its gap stated; the dossier is rebuilt and the
   rows are judged again. Whatever is still open is flagged in `ledger.md` for
   Ian, under the cross-row connections the agents found.
5. **Brief (05)** states the through-line first and assigns every row to a
   segment (`Ledger: L01`), with the entries it should use (`Dossier: D03,
   D07`). Facts the verify agents corrected are listed by code at the end.
6. **Coverage (07b, Sonnet 5.5, batched, fresh context)** checks the assembled draft against
   the ledger: each row `covered`, `thin`, `missing` or `repeated`, the
   segments it is in, and a note for Ian. It also reports drift (ideas that
   map to no row), whether the through-line is audible, and quotes not backed
   by a `verbatim: yes` entry. The editor (08) reads `07b_coverage.md`. No score.

**Quotes and tiers.** An agent marks a quote `verbatim: yes` only if it
copied the words from a page it fetched. The merge matches every such quote
word for word against that page, or against the transcript for the episode.
A quote that does not match is downgraded to `verbatim: no`, and the script
may quote only `verbatim: yes` entries. A segment's argument rests on tier 1
or 2 sources; tier 4 is color only. The source check at the end of the dossier
lists downgraded quotes, cited URLs no agent retrieved, entries without a tier
or URL, tiers better than a known domain allows (`research.tier_domains`), and
searches used per row.

Findings are paid for, so they are kept: `research` without `--rows` only
researches rows that have no findings yet. `--rows L01,L03` redoes those
rows. `--force` allows the dossier to be rebuilt. A row whose idea or question
changed in `ledger.json` after it was researched is flagged, and
`ledger-update` will not judge until every active row's research matches the
ledger. A row's files change only when its new findings are written: once a
redo lands, the row's old follow-up moves to `04_research/_superseded/`. A
write-up that fails keeps its tool loop's material in `Lnn.pending.json`, and
the next `research` run writes it up without paying for the loop again. The dossier's hash is
kept in `04_research/dossier.sha256`, and `ledger-update` stops before any
paid call if the dossier was edited by hand (its follow-up round rebuilds it).
Every call is logged with its row id. Spend in one invocation stops at `research.max_usd`.
All the knobs are under `research:` in `config.yaml`.

## How it works

- **Prompts** are plain markdown in `prompts/`, one per stage, with their inputs
  listed above a `---` line. Only the part below the line is sent.
  `prompts/style_guide.md` is the house style for draft, revise, edit and the
  baseline. The brief adds a style section for its chapter, which wins where
  the two disagree, because that is the version Ian edits.
  `prompts/source_tiers.md` holds the source-tier table every prompt that
  needs it includes.
- **Dossier markers.** Every dossier entry is numbered `[D01]`, `[D02]`, ...
  with its tags and source. The draft and revise stages tag every factual
  sentence with its entry number, e.g. `[D12]` or `[D03, D07]`. The editor
  checks each tag against the dossier. Narration strips the tags, and the
  report samples tagged sentences for the spot-check.
- **Segments** in the assembled script are separated by `<!-- segNN -->`
  markers, which narration never reads aloud.
- **Caching.** In the draft stage the style guide, brief and dossier form a
  cached system prompt shared by every segment call. The earlier segments get
  a second cache breakpoint. Revise works the same way.
- **Cost.** Every API call is logged to `runs/<run>/log.jsonl`. Each entry
  records tokens, cache reads and writes, searches, the model that actually
  served the call, and the cost in USD from the prices in `config.yaml`.
  Before any call or multi-call stage estimated above $5, the tool stops and
  asks. `--yes` pre-approves. The baseline's single tool loop can pause and
  resume. Before each resume the tool re-checks total spend and asks once if
  the projection passes $5. Research guards its whole budget up front instead.
- **Search budgets** (each research agent's, the baseline's 30) are set as the
  tool's `max_uses` and stated in the prompt. The API applies `max_uses` per
  request, and the tool list must stay byte-identical when a paused tool loop
  resumes (changing it breaks Opus 5.5's check on the earlier thinking). A
  research agent's loop is resumed only if it paused before using any tool;
  otherwise it is written up, so it never passes its budget. The baseline's
  loop can resume several times and pass its 30. The log records real counts.
- **Structured output.** The ledger, ledger-update and coverage stages ask for
  JSON with a schema (`output_config.format`). The smoketest checks this live.
- **Narration** sits behind one function, `stages/tts.py:synthesize`. Chunks
  split at paragraph breaks, then at sentences, below the model's per-request
  limit, which is read live from the account. They are stitched with
  `previous_request_ids` and `next_text`. Each chunk is cached on disk by
  content, so a re-run pays only for changed text. ffmpeg joins the chunks
  with short pauses, longer at segment breaks, and exports one MP3.
- **Refusal fallbacks** are on (`refusal_fallbacks: default`). If the model
  declines a request, the API retries it on a fallback model in the same call.
  The log records which model served it. Set the option to `null` to turn it
  off.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The tests run every stage end to end against fake Claude and ElevenLabs
clients, with real files and real ffmpeg. Covered: the ledger and Ian's hand
edits, research per row (the rank-1 agent first, budgets by rank and type, the
search cap, a paused agent written up rather than resumed once it has used its budget, a failed
agent retried alone), the merge (duplicates, renumbering, quote downgrades,
tier flags), the follow-up round and the code check on "filled", the brief's
checks and corrections, the coverage check, cache breakpoint placement,
keeping existing segments, marker stripping, the pronunciation table, chunk
caching, the blind compare and the report. They make no network calls.

## Not yet verified against the live APIs

- ElevenLabs facts were taken from SDK source and from search summaries of its
  docs, because elevenlabs.io was not reachable from the build machine. These
  include `eleven_v4` as the best narration model, its 10,000-character
  per-request limit, request stitching on v4, and the response's `request-id`
  header. `python cli.py voices` shows the live model list and limits.
- The three candidate voice IDs in `config.yaml` are ElevenLabs premade voices.
  Confirm them with `python cli.py voices`.
- `pcm_24000` output avoids the Pro-plan gate on 44.1 kHz PCM. Switch to
  `pcm_44100` on Pro.
- Ingest's chrome-stripping rules were tested on a synthetic PDF. Check
  `removed_lines.txt` on the real one.
