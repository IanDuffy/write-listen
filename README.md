# write-listen: commissioned audio chapter MVP

A small local pipeline that turns one podcast transcript into one researched,
edited, narrated 40-minute audio chapter, plus a single-prompt baseline to
compare it against in a blind listen. The spec is the build doc
(*Build Doc: Commissioned Audio Chapter MVP*, Nov 9, 2026).

Each stage reads files from a run folder and writes one new file, so any
output can be read and edited by hand before the next stage runs. A stage
refuses to run if its input is missing, and never overwrites an output
without `--force`.

## Setup

```bash
uv sync                       # creates .venv with the pinned versions in uv.lock
cp .env.example .env          # then add ANTHROPIC_API_KEY and ELEVENLABS_API_KEY
```

Needs Python 3.12+, `ffmpeg`, and ideally `pdftotext` (poppler); `pypdf` is the
fallback for PDF text. `.env` and everything under `runs/` are gitignored: the
transcript is copyrighted, and every output derives from it.

## Milestones

Each milestone stops for Ian.

1. **Skeleton.** `python cli.py ping` makes one tiny Haiku call and checks the
   ElevenLabs key (it prints the plan and character quota). Both are logged to
   `runs/ping/log.jsonl`.
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
4. **Research and brief.** `research` (web search and fetch, capped at 30
   searches), then `brief`. Edit `05_brief.md` by hand. Keep its
   `## Segment N: Title` headings and its `Target words:` and `Claims:` lines,
   because the draft stage reads them.
5. **Pilot.** `draft beauty-scarry --segments 1-2`, then
   `narrate beauty-scarry --pilot` gives `10_audio/pilot.mp3`. Adjust
   `prompts/style_guide.md` and the prompts from the listen.
6. **Draft the rest, assemble, edit, revise.** `draft` keeps the segments that
   already exist, so this drafts only the rest. Then run `assemble`, `edit` and
   `revise`, and read `09_script_final.md`.
7. **Narrate, baseline, compare, report.**
   `narrate`, `baseline`, `narrate --baseline`, `compare`, `report`.

`python cli.py status <run>` shows what exists, what's next, and spend so far.

## How it works

- **Prompts** are plain markdown in `prompts/`, one per stage, with their inputs
  listed above a `---` line. Only the part below the line is sent.
- **Claim markers.** The research stage numbers every claim `[C1]`, `[C2]`, ...
  with a source URL, then checks every cited URL against the URLs that search
  and fetch actually returned. The result is appended to `04_dossier.md`. The
  draft and revise stages tag every factual sentence with its claim number.
  The editor checks each tag against the dossier. Narration strips the tags,
  and the report samples tagged sentences for the spot-check.
- **Segments** in the assembled script are separated by `<!-- segNN -->`
  markers, which narration never reads aloud.
- **Caching.** In the draft stage the style guide, brief and dossier form a
  cached system prompt shared by every segment call. The earlier segments get
  a second cache breakpoint. Revise works the same way.
- **Cost.** Every API call is logged to `runs/<run>/log.jsonl`. Each entry
  records tokens, cache reads and writes, searches, the model that actually
  served the call, and the cost in USD from the prices in `config.yaml`.
  Before any call or multi-call stage estimated above $5, the tool stops and
  asks. `--yes` pre-approves.
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
clients, with real files and real ffmpeg. Covered: the `pause_turn`
continuation in research, cache breakpoint placement, keeping existing
segments, marker stripping, the pronunciation table, chunk caching, the blind
compare and the report. They make no network calls.

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
