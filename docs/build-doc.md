# Build Doc: Commissioned Audio Chapter MVP

Nov 9, 2026 · @Ian Duffy

## Goal and success criteria

Build a small local pipeline that turns one source (a podcast transcript) into one researched, edited, narrated 40-minute audio chapter. Then find out whether it is worth listening to.

**What the MVP must prove:** a staged pipeline (research, brief, segment drafts, edit) produces a chapter clearly better than a single-prompt baseline on the same angle.

**First run:** *Can Beauty Make Us Good?*, The Atlantic's *The Permanent Questions*, David Brooks with Elaine Scarry, posted November 8, 2026. Transcript supplied as a PDF.

**Success criteria, judged by Ian after listening:**

1. Finished the chapter without skipping.
2. Learned something the episode did not give him.
3. Wants chapter two.
4. Prefers the pipeline chapter to the baseline in a blind listen.
5. A spot-check of 10 factual claims against the dossier finds no invented facts.

If 1-4 fail, the idea is not worth building further. If only 5 fails, the fix is in research and fact-checking.

## Scope

| In | Out (later, if the test passes) |
| --- | --- |
| One source: a transcript (PDF or text) | Audio input and transcription (local Whisper when needed) |
| One chapter, about 6,000 words, about 40 minutes | Multi-chapter books, series |
| One narrator voice | Multiple voices, two-host format, music |
| A baseline chapter for comparison | Study-group of multiple models proposing angles |
| Plain files in a folder | Database (SQLite when there is more than one chapter) |
| Command-line runs, one stage at a time | Web interface, scheduling, podcast feed |
| One combined edit and fact-check pass | Automated scoring loops |

## Architecture

A Python command-line tool. Each stage reads files from a run folder and writes one new file, so Ian can read and edit any output before the next stage runs. No database, no server.

- **Language:** Python 3.12, in a virtualenv, managed with `uv` or `pip`.
- **Writing model:** Claude API via the official `anthropic` Python SDK. Opus 5.5 for research, brief, drafting and edit; Haiku 5.5 for cheap mechanical steps (cleanup, chunking).
- **Research:** the Claude API's server-side web search and web fetch tools. No separate search service.
- **Narration:** ElevenLabs API, using its highest-quality narration model. Keep narration behind one function so the provider can be swapped. Check ElevenLabs' current docs for the model ID, per-request character limit and pronunciation dictionary support.
- **Audio assembly:** `ffmpeg` to join segment WAVs and export one MP3.
- **PDF text:** `pypdf` or `pdftotext`.
- **Prompts:** one markdown file per stage in `prompts/`, never inline in code.
- **Config:** `config.yaml` for model IDs, voice, target lengths. API key in `.env`, gitignored.

### Folder layout

```
chapter-mvp/
  cli.py                 # entry point: python cli.py <stage> <run>
  stages/                # one module per stage
  prompts/               # one .md prompt per stage
  config.yaml
  .env                   # ANTHROPIC_API_KEY, ELEVENLABS_API_KEY (gitignored)
  runs/
    beauty-scarry/
      00_source/         # transcript.pdf, transcript.txt
      01_map.md
      02_angles.md
      03_angle.md        # Ian's choice, written by hand
      04_dossier.md
      05_brief.md
      06_segments/       # seg01.md ... seg08.md
      07_script_draft.md
      08_edit_notes.md
      09_script_final.md
      10_audio/          # seg WAVs + chapter.mp3
      baseline/          # script.md + chapter.mp3
      log.jsonl          # every API call: stage, model, tokens, cost
```

Each stage refuses to run if its input file is missing, and never overwrites an existing output without `--force`.

## Pipeline stages

Human checkpoints are marked **\[Ian\]**. The pipeline stops there and waits.

| # | Stage | Input | Output | Model | What it does |
| --- | --- | --- | --- | --- | --- |
| 0 | ingest | transcript PDF | `00_source/transcript.txt` | none | Extract text; strip page headers, URLs, site chrome, related-article lists |
| 1 | map | transcript | `01_map.md` | Opus | Every thinker, work, claim and example, with what each is used to argue; mark the threads the episode skims |
| 2 | angles | map | `02_angles.md` | Opus | 3-4 candidate chapter angles: thesis, why it is compelling, which threads it develops, what research it needs |
| 3 | choose **\[Ian\]** | angles | `03_angle.md` | none | Ian picks or edits one angle and adds any notes on what he wants from it |
| 4 | research | angle + map | `04_dossier.md` | Opus + web search/fetch | Primary texts first (Gutenberg, Stanford Encyclopedia of Philosophy), then reputable secondary sources. Every claim numbered with a source URL. Cap: 30 searches |
| 5 | brief | angle + dossier | `05_brief.md` | Opus | Thesis, 6-8 segments with purpose and target words, claims each segment must carry (by dossier number), listener profile (a curious non-specialist who expects the primary texts to be over his head), opening hook, closing line, style guide for the ear |
| — | review **\[Ian\]** | brief | edits to `05_brief.md` | none | Ian edits the brief by hand before drafting |
| 6 | draft | brief + dossier + earlier segments | `06_segments/segNN.md` | Opus | One call per segment, about 750-800 words each, in order. Each call sees the full brief, the style guide, its dossier claims and every earlier segment's full text |
| 7 | assemble | segments | `07_script_draft.md` | none | Concatenate |
| 8 | edit | draft + brief + dossier | `08_edit_notes.md` | Opus, fresh context | One editor pass: cuts, additions, reordering, repetition, pacing, tone drift, and every factual claim checked against the dossier. Output is notes, not a rewrite |
| 9 | revise | draft + notes | `09_script_final.md` | Opus | Apply the notes segment by segment |
| — | read **\[Ian\]** | final script | — | none | Ian reads the script before narration, so writing and voice are judged separately |
| 10 | narrate | final script | `10_audio/chapter.mp3` | ElevenLabs API | Split at paragraph breaks to fit the per-request limit, synthesize, join with short pauses at segment breaks, export MP3. Pronunciation table in config (Weil = "vay", Diotima, Boethius, Aquinas) |

### Style guide (starting point for the brief)

- Written for the ear: short sentences, one idea each, signposting between sections.
- Second person and concrete examples over abstraction. Every philosopher arrives with a scene or an image.
- No lists, headings or parenthetical asides in the script.
- Quotes from primary texts kept short and attributed aloud.
- One narrator, warm and plain, no performed enthusiasm.

## Baseline and evaluation

**Baseline stage** (`python cli.py baseline <run>`): one Opus call with the transcript and `03_angle.md` only, asked for a 6,000-word narration script. Web search is allowed, so the baseline is the best a single prompt can do. No dossier, no brief, no edit. Narrated with the same ElevenLabs voice and settings, saved to `baseline/`.

**Blind comparison:** the tool copies both MP3s to `compare/` as `A.mp3` and `B.mp3` in random order and writes the key to `compare/key.txt`. Ian listens before opening the key.

**Report** (`python cli.py report <run>`) writes `report.md` with:

- Word count and duration of both chapters.
- Total API cost per stage, from `log.jsonl`.
- Ten claims sampled at random from the final script, each with its dossier source, for Ian's spot-check.
- Blank lines for Ian's answers to the five success criteria.

## Tools and cost estimate

One full run (chapter plus baseline) should cost about $12-17: roughly $5-10 of Claude API usage, covered by the $250 in credits, plus about $7 of ElevenLabs narration, billed separately. These are rough estimates; `log.jsonl` will give the real Claude figures.

| Item | Tool | Price basis | Est. per run |
| --- | --- | --- | --- |
| Map, angles, brief | Claude Opus 5.5 | $4 / MTok in, $20 / MTok out | $0.50 |
| Research | Opus 5.5 + web search and fetch | $10 per 1,000 searches, plus tokens | $2.20 |
| Drafting, 8 segments | Opus 5.5 | as above | $1.50 |
| Edit and revise | Opus 5.5 | as above | $1.30 |
| Baseline, with web search | Opus 5.5 | as above | $1.00 |
| Narration, chapter + baseline | ElevenLabs API | about $0.10 per 1,000 characters (reported); about 36,000 characters per chapter | $7.20 |
| Audio assembly | ffmpeg | free | $0 |
| Transcription (not needed for this run) | faster-whisper, local | free | $0 |

Use prompt caching on the dossier and brief, which repeat across the segment calls. Cache hits on Opus 5.5 are billed at 0.05x the base input price.

The Claude API has no speech-to-text or text-to-speech. Claude prices are from the [Claude API pricing page](https://platform.claude.com/docs/en/about-claude/pricing), checked November 9, 2026. The ElevenLabs rate is from third-party pricing guides that disagree on plan details; confirm the plan and rate on elevenlabs.io before subscribing. Its free tier is reported at 10,000 credits a month, about 10 minutes of audio, which covers the voice test and pilot. OpenAI's text-to-speech models are reported to shut down on January 6, 2027, so the build does not depend on them.

## Build order for Claude Code

Build and verify each milestone before starting the next. Stop and report to Ian at the end of each.

1. **Skeleton.** `cli.py`, config loading, run-folder handling, API client wrapper that logs every call to `log.jsonl` with tokens and cost. Verify with one tiny test call.
2. **Voice test.** Wire up the ElevenLabs API and narrate the same 300-word sample paragraph about Diotima in three candidate voices. Check pronunciations. Send Ian the three MP3s; he picks the voice.
3. **Ingest, map, angles.** Run on the Scarry transcript. Stop at the **\[Ian\]** angle choice.
4. **Research and brief.** Run after Ian writes `03_angle.md`. Stop for Ian's brief review.
5. **Pilot.** Draft segments 1 and 2 only, about 10 minutes, and narrate them. Stop for Ian to listen. Adjust the style guide and prompts from his notes before drafting the rest.
6. **Draft the rest, assemble, edit, revise.** Stop for Ian to read the final script.
7. **Narrate, baseline, compare, report.**

Write prompts as plain markdown in `prompts/` with the inputs they expect listed at the top, so Ian can read and edit them without touching code.

## Rules and open questions

**Rules**

- Keep it small. No abstraction layers, plugin systems or frameworks beyond the SDK.
- Never put the API key in code, logs or prompts. `.env` is gitignored from the first commit.
- Stop and ask before any step expected to cost more than $5 in a single call or stage.
- The transcript is copyrighted. Keep it and all outputs local and private; quote from it sparingly in the script.
- Every factual claim in the final script must trace to a dossier entry. If the editor finds one that doesn't, it is cut or sourced.

**Open questions for Ian**

- Which ElevenLabs plan to use: start on the free tier for the voice test and pilot, then pick a paid plan for the full run.
- Narrator voice: decided at the voice test in milestone 2.
