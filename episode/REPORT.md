# Episode page: report

Built 2026-11-12 on branch `feature/episode-page`, hands-off, against the finished chapter-one run
(`runs/beauty-scarry`, 42:51). Everything the spec asks for is built; the image field is reserved
(`"image": null`). Integration notes and the differences from the spec are in `INTEGRATION.md`.

## Open it on chapter one

```bash
.venv/bin/python -m episode build beauty-scarry
.venv/bin/python -m http.server 8077
```

Then open <http://localhost:8077/episode/page/index.html?run=/runs/beauty-scarry>. Port 8000 was
already taken on this Mac by another process, hence 8077. The outputs already exist, so `build`
reuses its caches: no recognition, no model calls. First install the add-on's packages once:
`uv pip install -r requirements-episode.txt` and `.venv/bin/python -m playwright install chromium`.

## What was built

| Part | What it does |
| --- | --- |
| `episode/script.py` | The final script as segments, sentences and words, with shared ids (`seg02-s014`). Each word keeps its displayed form (the script) and its spoken form (after the narration's pronunciation table). Dossier markers attach to the sentence they follow. Initials ("C. D. C. Reeve") and abbreviations don't end sentences. |
| `episode/recognize.py` | faster-whisper `small.en` with word timestamps, behind one interface; a recorded recognizer for tests. Audio is decoded with ffmpeg; Apple's MPS backend runs at about 12x real time. |
| `episode/align.py` | Script words matched to recognized words: a word pass, a character pass inside gaps (split or joined words, numbers, respelled names), interpolation for the rest (`"estimated": true`). Times never go backwards, no word passes 2 s, nothing passes the audio's end. |
| `episode/timing.py` | Stage 1. Recognizes each narration chunk at its exact offset (from `10_audio/concat.txt`), or the whole file. Then any gap the long pass skipped is heard again in a short clip and realigned. Caches recognizer output. `nearest_sentence_boundary` and `current_word` for later features. |
| `episode/references.py` | Stage 2. One Claude Sonnet 5.5 call per segment; the model returns dossier ids, code takes every link from the dossier. Thinning: one card per sentence, no repeat of a source (same title, link, entry or surname) within 60 s. Answers are cached by prompt. |
| `episode/bundle.py` | Stage 3. `11_episode/episode.json`, the one file the page reads. |
| `episode/page/` | Stage 4. `index.html` plus `timing.js` (pure functions, tested under Node): play and pause, seek bar, clock, transcript in parts, sentence and word highlight, click a word to seek, follow with "Back to now", cards when spoken, space and arrows. |
| `episode/checks.py` | `python -m episode check <run>`: the end-to-end checks 8-12. |

## Tests (checks 1-7, on `tests/fixtures/run_small`)

`.venv/bin/python -m pytest -q`: **195 passed**, 23 of them in `tests/episode/`. That covers
every script word once and in order; sane times; displayed text exactly the script (with the
respelled "Diotima" kept as spoken text); `nearest_sentence_boundary` at 0, at the end and at
segment edges; references (sentence ids, dossier-only URLs, thinning, rejected ids and quotes);
the current-word function in Python and in the page's JavaScript, including gaps; and Playwright
confirming the highlight moves during playback and that clicking a word seeks the audio.

The fixture is two segments of three sentences cut from chapter one, chosen with no quotations
(the episode and the novels are copyrighted) and with the hard cases: "houseful", which Whisper
hears as "house full", and "Diotima", which narration respells. Its dossier is synthetic
(`example.org` links). The recorded recognizer output and model answers mean no model, audio work
or API call in the tests.

## End to end on chapter one (checks 8-12)

**8. Matching: 100% of 6,843 words have recognizer times** (6,785 exact, 58 from the character
pass, 0 estimated), so no stretch of estimated words remains. The first pass left 36 words
estimated, including two stretches of 15 and 12. Whisper had skipped those sentences in the
4-5 minute chunks but heard them perfectly in 9-second clips. So the timing stage now hears every
gap again in a short clip. It re-heard 5 gaps on chapter one, with no squeezed stretches left.

> **A correction to what I first reported:** I said the narration had dropped those two passages.
> It had not: the audio has them; the recognizer missed them. The review caught it by checking
> the audio's silences, and re-recognizing the clips confirmed it.

**9. Duration:** the last word ends at 2570.20 s; the file is 2570.52 s (0.32 s of trailing
silence). Within 1 s.

**10. Spot check: 19 of 20.** Twenty words spread evenly across the chapter (four letters or
more), each cut to one second around its time and recognized again. The miss was "then" at
38:25, heard as "Thank God." in the one-second cut. A first run's miss was "Alcibiades", heard as
"of acid biodes", at the right moment. Both are recognizer spellings of a clipped word, not
timing errors.

**11. Screenshots** at five points (they show the chapter's text, so they stay with the run,
which is not committed):

| Time | Highlighted word (sentence) | Newest card |
| --- | --- | --- |
| 2:08 | "love." (seg01-s026) | Symposium |
| 11:34 | "thing," (seg03-s036) | "to love one such form only" |
| 21:25 | "tools." (seg05-s015) | C. D. C. Reeve |
| 30:50 | "else?" (seg06-s059) | On Christian Doctrine I |
| 40:16 | "the" (seg08-s032) | Nussbaum |

Files: [seek1](../runs/beauty-scarry/11_episode/screenshots/seek1_128s.png),
[seek2](../runs/beauty-scarry/11_episode/screenshots/seek2_694s.png),
[seek3](../runs/beauty-scarry/11_episode/screenshots/seek3_1285s.png),
[seek4](../runs/beauty-scarry/11_episode/screenshots/seek4_1850s.png),
[seek5](../runs/beauty-scarry/11_episode/screenshots/seek5_2416s.png).

**12. References: 177 cards:** 103 people, 44 sources, 26 quotes, 4 works; 61 with a link, all
from the dossier. People cards carry no link: their entry is a source that mentions them, rarely
a page about them. The brief assigned 163 entries across segments; 33 never got attached:
- **32 are connection or tension entries.** These are the researchers' own reasoning, with no
  source behind them, and are never shown as sources, by design.
- **1 (seg07 D207) the script never cites.**
- **None** were cited by the script and missed.

## Cost

Claude: **$0.66** for 9 Sonnet 5.5 calls (8 segments, plus seg05 again after the sentence-split
fix), against a $3 cap. Logged in `runs/beauty-scarry/11_episode/log.jsonl`. ElevenLabs: none.
Whisper runs locally: about 3.5 minutes for the chapter on MPS, then cached.

## Review

One adversarial review (four lenses: timing, references, page, spec audit; each finding checked
by a skeptic) confirmed 16 findings, all fixed with regression tests. In brief:
- **Timing:** the skipped-gap repair (above); estimated times clamped to the audio; initials no
  longer split sentences; the recognizer cache keyed by model.
- **Links:** "same URL as Dnn" back-references followed; people cards unlinked; surname repeats
  caught.
- **Page:** follow mode pauses on any page scroll but not the cards panel; keys work after the
  seek bar is used and leave Back and Forward alone; the right arrow in the last sentence stays
  put; the card list updates one card at a time.
- **INTEGRATION.md:** a broken snippet fixed.

## Not built (out of scope or not allowed here)

- Images for references.
- The interruption feature (it will call `nearest_sentence_boundary`).
- Streaming.
- The `elevenlabs` timing provider. A small live test since (about 500 characters, with Ian's
  OK) confirmed that `eleven_v4` returns character timings with narration's exact settings and
  stitching. They are on a 40 ms grid and within a median 0.10-0.15 s of Whisper's word starts.
  The narration change and the provider are described in `INTEGRATION.md` §3; building them means
  editing the narration stage, which this branch had to leave alone.
