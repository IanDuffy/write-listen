# Episode page: integration notes

Built on branch `feature/episode-page` (from `claude/new-project-foundations-k7jbg5`, the
default branch) as new code only: `episode/`, `tests/episode/`, `tests/fixtures/run_small/`,
`requirements-episode.txt`. Nothing in `cli.py`, the stages, `server.py` or `page/` was edited;
`episode/` imports the pipeline's helpers read-only (`stages.common`, `stages.tts`,
`stages.brief`, `stages.research`, `stages.llm`).

## 1. Register the commands in `cli.py`

In the module docstring, under the run commands:

```
    episode <run>                timed transcript, references, episode.json -> 11_episode/ (python -m episode build)
```

In `main()`, next to the other run stages (after `r = Run.open(...)`):

```python
        elif st == "episode":
            from episode.__main__ import main as episode_main
            return episode_main(["build", str(r.root)])
```

and add `("episode", "episode")` after `("chapter_mp3", "narrate")` in `STAGES_IN_ORDER`,
with `"episode": "11_episode/episode.json"` in `stages/common.PATHS`. For an unattended seed
run, add a step after `narrate` in `stages/new.py`'s step list, with a small wrapper there so a
failed build stops the run (`main()` returns an exit code, which the step runner would ignore):

```python
def episode_step(r, cfg):
    from episode.__main__ import main as episode_main
    if episode_main(["build", str(r.root)]) != 0:
        raise StageError("the episode page could not be built (see run.log)")

    ("episode", "episode", episode_step, "Finishing"),
```

## 2. Link to the episode page from the onboarding page

`server.py` serves the page and the runs, both with FastAPI's `StaticFiles`, which handles range
requests (so the audio streams instead of being fetched whole):

```python
from fastapi.staticfiles import StaticFiles
app.mount("/episode", StaticFiles(directory=ROOT / "episode" / "page", html=True), name="episode")
app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")
```

In `page/index.html`, when the chapter is ready (`onStatus`, `s.state === "ready"`), show a link
under the title:

```js
`<a href="/episode/index.html?run=/runs/${encodeURIComponent(s.run)}">Read along</a>`
```

The server listens on 127.0.0.1 only, so serving `runs/` stays on the laptop. Until then:
`python -m http.server 8077` from the repository root, then
`http://localhost:8077/episode/page/index.html?run=/runs/<run>`.

## 3. Timings from ElevenLabs at generation time (provider built; narration change at merge)

**What the docs confirm (checked 2026-11-12):** `POST /v1/text-to-speech/{voice_id}/with-timestamps`
(SDK: `client.text_to_speech.convert_with_timestamps`, also `stream_with_timestamps`) takes the
same body as the endpoint narration uses now, including `previous_request_ids`, `next_text` and
`previous_text` for stitching and `output_format: pcm_24000`, and returns `audio_base64` plus
`alignment` and `normalized_alignment`, each `{characters, character_start_times_seconds,
character_end_times_seconds}`.

**Confirmed live (2026-11-12, Ian's OK for a small test; about 500 characters):** `eleven_v4`
returns both alignments, one entry per character of the text sent, with narration's exact settings
(voice, `pcm_24000`, voice settings, seed). The `request-id` header comes back, and a request
stitched with `previous_request_ids` works with timestamps, so narration's continuity is kept.
Details that matter for the code:
- In the Python SDK (2.77) the audio field is `audio_base_64` (the REST docs say `audio_base64`),
  and `with_raw_response.convert_with_timestamps(...)` returns the response directly: it is not a
  context manager, unlike the streaming `with_raw_response.convert` narration uses now.
- Times come on a 40 ms grid. On two chapter-one lines, word starts were within a median
  0.10-0.15 s of Whisper's (largest 0.36 s) and word ends within 0.06 s: close enough for the
  highlight. ElevenLabs gives the pause after a punctuation mark to the word before it ("love."
  ends 0.5 s later than Whisper hears it), so a sentence's end time includes its pause.
- The characters are the text that was sent, i.e. the spoken form (after pronunciations), so
  they map onto `episode.script`'s spoken words directly; the displayed words come from there.

**Which is more accurate (decided 2026-11-12, Ian left it to me): ElevenLabs.** Against the
audio's own silences in the two test clips (ffmpeg `silencedetect`, the four pauses inside speech),
ElevenLabs' word ends fell within 0.05 s of where the silence starts and the next word's start
within 0.02 s of where it ends; Whisper ended words 0.15-0.2 s early (starts within 0.1 s). That
counts a word as ending on its last letter, not on the punctuation mark that holds the pause. It
is a small sample, but it also matches the larger point: ElevenLabs times the text it was given,
so it cannot skip a stretch the way Whisper skipped two in chapter one's long chunks.

**Built on this branch** (`episode/timing.py`, tests in `tests/episode/test_elevenlabs_timings.py`):
- `ElevenLabsTimings` reads `alignment` from each chunk's existing `chunks/<key>.json` (the file
  narration already writes with `request_id`, `segment`, `text`), splits the characters into
  words at spaces, and ends each word on its last letter or digit. It has the recognizer's
  interface, so the chunk offsets from `concat.txt`, the aligner, `estimated` marking and stages
  2-4 are unchanged. Nothing is cached (the timings are already on disk) and gaps are not heard
  again (a clip has no timings of its own).
- `pick_timings(run, mode, make_recognizer)`, and `python -m episode timing|build --timings
  auto|elevenlabs|whisper`. `auto` (the default) uses ElevenLabs' timings when every chunk has
  them, else Whisper. So chapter one and every run narrated before the change keep Whisper with
  no re-narration; `elevenlabs` stops with a message if any chunk lacks them.
- Checked on the live test's real output: every word matched (the respelled "Diotima" through
  the character pass), none estimated.

**The narration change, at merge** (`stages/tts.py`; not done here, since this branch leaves the
stages alone):
- `synthesize()` calls `convert_with_timestamps` through `with_raw_response`, keeping the
  `request-id` header stitching needs: `r = client.text_to_speech.with_raw_response.convert_with_timestamps(...)`,
  then `r.data`, `r.headers` (not a context manager). It decodes `r.data.audio_base_64` and returns
  `r.data.alignment` (as a dict) in `Synth`.
- `narrate_segments()` adds `"alignment": syn.alignment` to the chunk's `.json`. Same characters
  billed; the cache key does not change, so cached chunks are reused (they just lack timings, and
  `auto` falls back to Whisper for that run).
- Whisper stays as the audit: `python -m episode check` (check 10) re-hears 20 words in one-second
  clips, which tests the timings against what was actually said, whichever source made them.

## 4. Where the repo differed from the spec

- **File names:** as the spec says (`09_script_final.md`, `06_segments/`, `10_audio/chapter.mp3`,
  `04_dossier.md`, `ledger.json`, `05_brief.md` with per-segment `Dossier:` lines).
- **No per-segment audio:** narration writes one WAV per chunk (about 4,500 characters, several
  per segment) to `10_audio/chunks/` and joins them with silences listed in `10_audio/concat.txt`.
  Timing uses that list (exact offsets, one recognizer pass per chunk) when it matches the MP3's
  length, else the whole file; segment boundaries come from the script either way.
- **Pronunciations:** `narration.pronunciations` in `config.yaml` (Diotima, Weil, ...); the
  spoken form is kept per word (`"spoken"`) where it differs. Editorial brackets in quotes are
  made speakable the same way narration does (`stages.tts.speak_editorial_brackets`).
- **Markdown in the script:** titles are in `*italics*`. `timing.json` keeps the script's text
  exactly (asterisks included); the page renders those words in italics.
- **Dossier markers:** the final script tags sentences `[D12]`, so most sentence-to-source links
  are known before any model call; the model confirms them and adds named works, people and quotes.
  Connection and tension entries (the researchers' reasoning) never become source cards.
- **"Same source" for the 60-second rule:** the same title, link or dossier entry (two entries
  can be two hosted copies of one dialogue).
- **faster-whisper and PyAV:** faster-whisper 1.2.1's decoder passes an argument the installed
  PyAV dropped, so audio is decoded with ffmpeg (already a dependency) and passed as samples.
  Apple's MPS backend runs at about 12x real time here (CPU about 6x); `--device auto` tries it first.
- **`python -m http.server` has no range requests,** so Chrome cannot seek audio served by it.
  The page fetches the audio whole (a blob URL); under a server with ranges it would stream.
- **Dependencies:** `uv pip install -r requirements-episode.txt`. A later `uv sync` removes them
  (they are not in `pyproject.toml`); the browser test then skips. When merging, add them as an
  optional group, e.g. `[project.optional-dependencies] episode = [...]`.
- **Prompt location:** `episode/prompts/references.md`, in the pipeline's prompt format, since
  this build touches nothing outside `episode/`. It can move to `prompts/` when merged.
- **Logging:** the references calls go through `stages.llm` and log to `11_episode/log.jsonl`,
  never the run's own `log.jsonl`.
- **Added:** `python -m episode check <run>` runs the end-to-end checks (8-12).
