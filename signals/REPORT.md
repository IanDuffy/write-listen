# Listener Signals: report

Ian's build plan: "Listener Signals Build Plan" (2026-11-21, Claude Docs). Built 2026-11-22 on branch
`ccr-bfa36549-2hocwb`, from `feature/tester-demo` at `b1d44ac`. Not merged anywhere.

**In short.** Everything in the plan's packages 1 to 6 is built and tested offline on synthetic audio and fake
conversations. None of it has run on a real conversation: this cloud session has no `listeners/` folder and cannot
reach OpenAI, so the plan's "what the measures did on the real conversations, and how they lined up with Ian's marks"
is still open. Both flags are off, so nothing a tester touches has changed. The first tester recordings expire on
**2026-12-16**: run `wl signals fetch --all` on the droplet before then (below), and again within 30 days of every
later visit.

## What to run, in order

On the droplet (the testers' folders live there). `wl deploy` pulls the droplet's own branch
(`feature/tester-demo`), so this branch has to be merged into it first (it is a fast-forward from `b1d44ac`), then
`ssh -t wl-droplet sudo wl deploy`:

```bash
ssh -t wl-droplet sudo wl signals fetch --all    # keeps every recording; says why for any it can't get
ssh -t wl-droplet sudo wl signals run --all      # one record per listener turn, from the recordings
ssh -t wl-droplet sudo wl signals report --all   # tags, bleed, phones, the norm, what was mentioned, your marks
```

Then open `/admin`, click **Signals** on a tester's row, pick a conversation or **the whole visit**, listen
(**Play from m:ss** under a point) and click the chart where you hear something to leave a mark ("lost them here").
`wl signals report` lists the turns near each mark. Turn on a flag only after that (below).

Locally the same commands work for your own listener (`wl signals fetch`, `run`, `report`, default `own`).

`fetch` says what happened to each recording: `have` (already kept, or hard-linked from a copy on disk), `fetched`,
`missing` (OpenAI has none: past its 30 days, or never stored), or `error` (anything else; run it again). A refused
key (401/403) stops the run's requests at once. A rerun only downloads what is new.

## What was built

| Package | Files | State |
| --- | --- | --- |
| 1. Keep the recordings | `signals/fetch.py`, `wl signals fetch`, `voice/gpt_live/session.py` `recording(why=)` | Built, tested with a fake OpenAI client |
| 2. Record, norm, note | `signals/record.py`, `config.yaml` `signals:` | Built, fixture tests |
| 3. Replay measurer | `signals/measure.py`, `signals/store.py`, `wl signals run [lid] [--all]` | Built, tested on synthetic two-channel WAVs |
| 4. Admin timeline and marks | `signals/routes.py`, `page/admin_signals.html`, `page/admin.html` | Built, tested offline and in headless Chromium |
| 5. Live measuring | `page/sig.js`, `page/live.js`, `page/interview.js`, `Line.sig` in both line routes, `signals/live.py` | Built behind `signals.live` (off) |
| 6. Earpiece note | `voice/gpt_live/director.py`, `prompts/live_director_signals.md`, `prompts/live_director_moment.md` | Built behind `live.director.signals` (off) |
| 7. Report | this file, a section in `CLAUDE.md`, `NEEDS_HUMAN.md` | Written; the real-data part waits for the commands above |

Also `wl signals report` (`signals/report.py`), which answers the plan's open questions from the data once it is on
disk.

### The record

One JSON line per listener turn in `<listener>/signals/<stem>.jsonl`, beside `sessions/` and `study/`, never inside
them (`memory.meeting()` counts any `sessions/*.jsonl` as a conversation; `demo/state.py` reads `study/*.jsonl` as
activity). `<stem>` is the transcript's: `20261121-101500` for a guide conversation, `intro-<sid>` and
`debrief-<sid>` for the interviewer's. Fields: `stem, part, source (recording|page), turn, live_id, t, start_s,
end_s, text`, the measures (`gap_ms, dur_ms, speak_ms, words, wps, pauses, longest_pause_ms, hedges, fillers, tags,
trailed, cut_in, rms_db, pitch_sd_st`; null when not taken, never a guess), and what the norm adds (`norm,
norm_turns, change, trend, mention, note`). A recording record lists the page turns it covers in `replaces`, and
those page records drop out when read. Marks go in `<stem>.marks.jsonl`, recordings in `signals/audio/<live_id>.wav`.

The note, the earpiece's one line, reports numbers beside the norm and never names a feeling:

> Their last turn: 4 words (norm 12); 2.4 s before answering (norm 0.6 s); 6 dB quieter than their norm; three
> turns running: shorter, quieter.

Before three turns of three words or more: "Their last turn: no norm yet (2 of 3 turns)." With nothing past a
threshold: "Their last turn: nothing outside their norm." Thresholds are the plan's (`config.yaml` `signals.thresholds`).

### Decisions and departures from the plan

- **Audio is kept per GPT-Live session, not per conversation.** A guide conversation spans several sessions (each
  rest closes one, each resume opens another), so `signals/audio/<live_id>.wav`, and records carry `live_id`.
- **The guide's onboarding recordings were not kept anywhere** (`voice/gpt_live/archive.py` fetches only the
  sessions a chapter's `reading.jsonl` names), so `wl signals fetch` downloads them. Copies already on disk (a
  chapter's `live/<id>.wav`, the interviewer's `study/<id>.wav`) are hard-linked instead. The interviewer's
  `study/<id>.mp3` is lossy, so the WAV is still fetched while OpenAI has it; the mp3 is the fallback.
- **The norm is per source.** The page and a recording are different instruments (the page's mic has echo
  cancellation and gain control on), so a page record is judged against earlier page records and a recording record
  against earlier recording records. Both carry across the visit: intro, guide and debrief in time order.
- **Two flags.** `signals.live` turns on measuring on the page (records only, nothing reads them live);
  `live.director.signals` turns on the earpiece's note and the measuring it needs. Off, `/live.js` and
  `/interview.js` are served byte for byte as before and the line post is still `{session, who, text}` (tests check).
- **The page measures with its own sampler** (`page/sig.js`), not `dirAudio`'s meters: those average over 600 ms,
  too coarse for a pause before answering. Its rules are the replay's (`signals/measure.py`), including bleed.
  Pitch stays replay-only, as the plan says.
- **The look and the line race.** The page posts the listener's line (with its measures) and asks for a look at
  about the same moment. The look carries `sig_turn`, and `director.heard` waits up to `signals_wait_ms` (400) for
  that turn's record, outside the conversation lock. A record from an earlier turn gives no note rather than a
  stale one, and `sig_turn` 0 (a typed line came last, or nothing has been said) gives none at once.
- **The earpiece's prompts are untouched with the flag off.** What the note means is in
  `prompts/live_director_signals.md`, appended to the earpiece's system prompt only with `live.director.signals` on;
  off, the system prompt, each look and each director row are byte for byte what they were at `b1d44ac`.
- **`director.look()` stays pure.** `ask()` reads the note and passes the block in; with no note `{{signals}}` is
  empty and the look renders byte for byte as before.
- **Bleed** in the replay also covers 150 ms after the other voice stops (`bleed_tail_ms`): without it the room's
  echo of the guide merged into the listener's next turn and shortened the pause.
- **The page follows the replay's turn rule.** A line that continues the listener's own speech (one utterance
  split into two lines by the transcript's timing) gets no pause before answering, as the replay would give it; a
  resume after a rest starts a new session, so its first turn is not timed from the guide's last words before the
  rest; and a sound under the guide (a laugh, an "mm", an echo of the guide on a loudspeaker) never stands in for the
  answer or reads as a cut-in. Of a line's voiced parts the page keeps the one the replay would give the line's text
  to (nearest the speech end the line implies, the replay's cost), so the two agree on which sound is the answer.
  The page decides one line at a time at the nominal 2.5 s lag, where the replay fits each session's lag, so turns
  whose transcript runs far from that lag can still differ; the report's "Page against recording" shows where.
- **A replay hides page records only from view.** Where a recording record replaces page turns, the timeline shows
  the recording's; the page norm still counts the page turns, so a later page turn keeps the norm (and the note)
  the earpiece was given, and the timeline shows that note.
- **A reset tester stays reset.** `wl signals run` and `fetch` check the listener folder is still the one they
  started on (its inode) before every write; a folder reset or deleted mid-run gets nothing, and the command says so.
- **Text to turns** in the replay: the transcript's rows are matched to audio turns in order, by nearest time, with
  the transcript's lag searched from -3 to +4 s around 2.5 s per session. Audio turns with no row (sounds GPT-Live
  never transcribed) are dropped.
- **Nothing in `signals/` calls a model.** The earpiece's note adds about 30 words to a look when its flag is on.

## The plan's open questions

| Question | Answer |
| --- | --- |
| Are the guide's recordings kept? | No. `wl signals fetch` keeps them. |
| Do `study/<id>.mp3` files keep both channels? | Yes (ffmpeg keeps the channel count); the WAV is still preferred. |
| How do transcript lines map to the recording's clock? | The session row's time is the recording's zero; a row lands about 2.5 s after the speech it holds ends; the replay searches the lag per session. A turn's `t` is good to about a second. |
| Which sound tags appear, and how often? | `wl signals report` counts them. Not run on real data yet. |
| How much does the guide bleed into the listener channel on a phone? | `wl signals report` gives it per recording, in dB, beside the phone and loudspeaker from `audio_route`. Not run yet. |
| Is the intro long enough to count toward the norm? | It counts (tests: a debrief starts with a norm built from intro and guide turns); with one or two short answers it often adds nothing, since a turn needs three words. `wl signals report` says where each listener's norm first appeared. |
| For Ian: the debrief earpiece | Not built (the plan's out of scope). The interviewer's page posts the same measures, so its records exist for whoever builds it. |

## Not checked, and what to watch for

- **No real audio yet.** `min_rms` (200) and `floor_x` (4) for the replay, and the page's `min_rms` (0.01), are
  starting values. If a quiet speaker's turns go missing or a noisy room makes turns run together, these are the
  knobs (`config.yaml` `signals.recording`, `signals.page`).
- **Loudness is weak evidence** on the page (gain control); the replay's is the recording's, after the same
  processing.
- **Turn edges on the page** follow the transcript, which lags the voice by 2 to 3 s, so speech that starts in that
  lag can be counted in the previous line. The guide's voice between turns usually separates them.
- **The page's record agrees with the replay's** (package 5's done-check) is untested on real audio: it needs one
  real conversation with `signals.live: true`, then `wl signals run`, then `wl signals report`, whose "Page against
  recording" section puts each page turn beside the recording turn that replaced it (differences in start, end, gap,
  length and words, how many times fall within 300 ms) and lists page turns the replay matched to nothing, with why.
- **The visit view on a long folder.** Your own folder can span days of chapters. The view zooms to 4 pixels a second
  for up to about 46 days of axis, less after that (browsers cap a page's width), and draws only the stretch on
  screen. Collapsing long idle gaps on the axis would fix it for good, but marks are stored as seconds on the axis,
  so that is your call. Testers' visits are under an hour and unaffected.
- **Disk:** about 5.8 MB a minute of conversation in `signals/audio/`.

## Turning the flags on

1. `signals.live: true` in `config.yaml` (or on the droplet's config) records page measures for every conversation.
   Nothing reads them live; the admin timeline shows them. No cost.
2. `live.director.signals: true` gives each earpiece look its note. Each look's director row records it as
   `heard`, and the signals timeline shows it in the look's panel ("The note it had"). Cost: about 30 more input
   words a look, and the section in `prompts/live_director_signals.md` once per conversation (cached).

## Review

After the build, five reviewers read the change from different sides (the replay, the page, the server and
earpiece, the admin view, the plan), and every finding was checked by two skeptics. Twelve were confirmed (two
pairs were the same bug), all fixed with regression tests, each fix checked again by someone who had not written it:

- `wl signals run` could write an old visit's records into a tester's folder reset while it ran.
- `fetch` called any failed download "missing" and blamed expiry; a bad key or a passing server error now reads as an
  error to retry.
- `report` left out marks on conversations with no records yet.
- On the page: a pause invented when one utterance was split into two lines; a rest's length counted as a pause;
  a sound under the guide's reply taken for the answer and called a cut-in.
- After a replay, the page norm lost the page turns the replay covered.
- With the earpiece's flag off its system prompt still changed (now in its own file, appended only when on).
- After a typed line, the look described the previous spoken turn as their last.
- On the timeline: lines with no record stopped later turns linking to theirs; a later line could be linked to an
  earlier turn.

Two more rounds checked the fixes themselves, each finding again confirmed by a skeptic before it was fixed: the
page's choice of which sound is the answer (first "the most voiced", which let a laugh beat a short "yeah"; now the
replay's own rule, checked against the replay in 41 scenarios and a sweep of transcript timings), a whisper's fate
mark lost when zoomed in, two turns under a second apart paired wrongly in the page-against-recording comparison, and
that comparison calling a measured recording "missing" when the replay heard no turn in it (now it says the mic may
be too quiet for `signals.recording.min_rms`).

`tests/live`, `tests/demo`, `tests/signals` and the server tests were run together twice (943 tests). Each time
one or two page tests that wait on timers failed under load (review agents were sharing the four CPUs); each passes
alone three times running, and with the flags off none of them reaches signals code. The known root-container
failure in `tests/demo/test_hosting.py` is unrelated.

## Tests

`tests/signals/` (offline, numpy test audio, fake clients):

```bash
TZ=America/New_York .venv/bin/python -m pytest tests/signals -q
```
