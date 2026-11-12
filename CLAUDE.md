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
`assemble`, `coverage`, `edit`, `factcheck`, `revise`, `narrate`; then `baseline`,
`narrate --baseline`, `compare`, `report`. `--yes` passes the $5 guard and
needs Ian's say-so for that run (he gave it for `beauty-scarry` on 2026-11-11).
Check the script's character count against the ElevenLabs balance before
narrating: the `starter` plan has 40,000 characters a month and one chapter is
about 36,000, so the baseline narration needs a bigger plan or the next month.

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
  Stage 04 runs one Opus 5.5 agent per row (`models.researcher`), budgeted by
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

## Layout

- `cli.py`: entry point; `python cli.py <stage> <run>`.
- `stages/common.py`: run folders, file guards (missing input stops; never
  overwrite without `--force`), `log.jsonl`, prompt loading, text helpers.
- `stages/llm.py`: the only Claude caller. Streams; logs tokens and cost per
  call; cost guard; retries dropped streams and overloads; resumes `pause_turn`.
- `stages/tts.py`: the only ElevenLabs caller (`synthesize`), chunking,
  pronunciation table, on-disk chunk cache, ffmpeg join.
- One module per stage; `ledger.py` (03b and the ledger data model),
  `research.py` (04 and the merge), `ledger_update.py` (04b), `coverage.py`
  (07b); `estimate.py`, `smoketest.py`, `voicetest.py`, `ping.py`.
- `config.yaml`: models, per-stage settings, research budgets, prices, narration.
- `docs/build-doc.md`: the spec; `docs/ledger-research-addon.md`: its research add-on.

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
keys can't be added there (Anthropic hosts bypass the proxy), which is why
work moved to a local environment.
