# CLAUDE.md: context for agents working on write-listen

Read this first, then `docs/build-doc.md` (Ian's build doc, the spec) and
`README.md` (how to run it). Ian does not build or run this himself: an agent
drives the pipeline with him, stopping at his checkpoints.

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
| 1. Skeleton | Built. `ping` and `smoketest` ready. ElevenLabs half of `smoketest` passed live; Anthropic half not yet run (no key yet). |
| 2. Voice test | Built (`voices`, `voicetest`). Not run. Candidate voices George, Brian, Sarah confirmed to exist on the account. Ian picks one, then set `narration.voice_id`. |
| 3. Ingest, map, angles | Built, tested offline. Waiting for the transcript PDF from Ian. |
| 4. Research, brief | Built (deep research, see below), tested offline. |
| 5. Pilot | Built (`draft --segments 1-2`, `narrate --pilot`). |
| 6. Draft rest, assemble, edit, revise | Built. |
| 7. Narrate, baseline, compare, report | Built. |

All 52 offline tests pass (`.venv/bin/python -m pytest -q`). They run every
stage end to end against fake Claude and ElevenLabs clients (`tests/fakes.py`)
with real files and real ffmpeg. Nothing has run against the live Claude API.

**Next steps, in order:**
1. Local setup (below), then `python cli.py ping` and `python cli.py smoketest`.
   Fix anything it marks FAIL before spending more.
2. `python cli.py voicetest`, send Ian the three MP3s in `voice_test/`, set
   `narration.voice_id` from his pick, adjust `narration.pronunciations`.
3. Get the transcript PDF from Ian: `python cli.py init beauty-scarry --source <pdf>`,
   check `runs/beauty-scarry/00_source/removed_lines.txt`, then `map`, `angles`.
   Stop: Ian writes `03_angle.md`.
4. `research beauty-scarry --plan-only`, show Ian the plan and the estimate,
   then `research`, then `brief`. Stop: Ian edits `05_brief.md`.

## Local setup

```bash
uv sync                      # Python 3.12+; pinned versions in uv.lock
brew install ffmpeg poppler  # macOS; on Linux: apt install ffmpeg poppler-utils
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

- **Deep research replaced the single 30-search research call** (Ian asked:
  30 searches is not enough; wants topics broken down and multiple agents;
  Sonnet allowed for this phase). `stages/research.py`: Opus plans 5-8 topics
  (`04_research/plan.md`, editable; `--plan-only`), one Sonnet 5.5 agent per
  topic runs in parallel (4 at a time, 25 searches and 25 fetches each), an
  Opus critic commissions follow-up topics for up to 2 more rounds, then code
  renumbers every `[Tn.k]` claim into `[Cn]` in `04_dossier.md` (no model
  rewrites sources or quotes) and Opus writes the overview. Built on the
  Messages API rather than Managed Agents so every search result and fetched
  page stays in the run folder. The baseline keeps the doc's 30-search cap.
- **Quote check.** Every quote in the dossier is matched word for word against
  the text of the page it was fetched from (`research.quote_check`), and cited
  URLs that no agent retrieved are flagged. Results are appended to the dossier
  as "Source check". This serves success criterion 5.
- **Claim markers.** Draft and revise tag every factual sentence `[C12]` or
  `[C3, C7]`. The editor checks them, narration strips them, the report samples
  them for the 10-claim spot-check.
- **Style guide precedence.** `prompts/style_guide.md` is the house style; the
  brief's own style section wins where they disagree, because Ian edits the brief.
- **Refusal fallbacks are on** (`refusal_fallbacks: default`, server-side
  fallback beta). The log records which model served each call.
- **Cost.** `python cli.py estimate` forecasts about $36 upper end for a full
  run (research about $23 if every critic round runs, ElevenLabs about $8),
  against the doc's $12-17; deep research is the difference. Ian has $250 in
  Claude credits.

## Rules (from Ian and the build doc)

- Stop at every [Ian] checkpoint and at the end of each milestone, and report.
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
| research plan, critic output | `## T3: Title` sections | `research.parse_topics` |
| topic findings | inside `<findings>`; claims `[T3.1] ... Source: URL` in `## Claims`; `## Conflicts`, `## Could not establish` | `research.topic_claims`, `merge_claims` |
| dossier | `[C12] claim. Source: URL` at line start inside `## Claims`; `Source: episode` for episode-only | `common.dossier_entries`, `research.source_check` |
| brief | `## Segment N: Title` with `Target words:` and `Claims: C3, C7` lines | `brief.parse_segments` |
| script | `<!-- segNN -->` before each segment; `[Cn]` markers after sentences | `common.split_script_segments`, `spoken_text` |
| edit notes | `## Global`, `## seg01` (or `## Segment 1`) sections | `revise.parse_notes` |
| baseline | inside `<script>` tags | `baseline.run` |

## Layout

- `cli.py`: entry point; `python cli.py <stage> <run>`.
- `stages/common.py`: run folders, file guards (missing input stops; never
  overwrite without `--force`), `log.jsonl`, prompt loading, text helpers.
- `stages/llm.py`: the only Claude caller. Streams; logs tokens and cost per
  call; cost guard; retries dropped streams and overloads; resumes `pause_turn`.
- `stages/tts.py`: the only ElevenLabs caller (`synthesize`), chunking,
  pronunciation table, on-disk chunk cache, ffmpeg join.
- One module per stage; `estimate.py`, `smoketest.py`, `voicetest.py`, `ping.py`.
- `config.yaml`: models, per-stage settings, research budgets, prices, narration.
- `docs/build-doc.md`: the spec.

## API facts that matter (checked against installed SDKs, anthropic 2.7, elevenlabs 2.77)

- Opus 5.5 and Sonnet 5.5: thinking can't be disabled (no `budget_tokens`, no
  `disabled`); control depth with `output_config.effort` (Opus 5.5 defaults to
  `medium`, so stages set it). No `temperature`. Forced `tool_choice` returns 400.
- Server web tools `web_search_20260209` / `web_fetch_20260209`. `max_uses` is
  per request. When a tool loop hits `pause_turn`, resend the turn with the
  tool list byte-identical: editing tools between continuations breaks the
  thinking-block check. The research spend cap is checked before every request.
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
