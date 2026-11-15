# Spec: Overnight Chapters

Nov 15, 2026 · @Ian Duffy

## Overview

Every weeknight the app builds the listener's next 20-minute chapter, so it is ready in the morning. Each evening an Up next card shows what tonight's chapter will be. The listener can change it by talking to the guide until 2am. In the morning the listener presses play, the guide gives a short live intro, and the chapter begins.

**Decisions**

| Decision | Setting |
| --- | --- |
| Build days | Weeknights: the build runs at 2:00 on Mon–Fri mornings |
| Change cutoff | 2:00, when the build starts. Later changes apply to the next build |
| Storage | Everything is kept: audio, scripts, transcripts, research, references |
| Cost | No budget. A runaway stop only (default $50 a night) |
| Where it runs | This laptop for testing. Written so the same command runs in the cloud later |
| Playback | Latest chapter only. Older chapters are stored but not playable yet |
| Listener approval | None. Up next builds whether or not the listener looks at it |

**Decision made in this spec (change it if you disagree):** the chapter is recorded overnight with GPT-Live, and the recording is what plays in the morning. Only the intro and any questions are live. This gives a saved, checked recording every time and keeps a failed read out of the morning. The fallback is reading live, which already works (check 7).

**Reused, not rebuilt**

- Live voice session and onboarding conversation (merged).
- Ask by voice: tap to pause, connecting state, resume with rewind.
- Ledger research, outline, rolling write and paired edit (fast start).
- Episode bundle and page (timings, transcript, references).
- GPT-Live overnight recording, checks 6 and 10 from the One Live Voice spec. Those checks are built in this phase.

## Listener profiles and storage

Each listener is a folder. Everything the app makes for a listener lives inside it, and nothing is ever deleted except by an explicit reset of a demo listener.

```
listeners/
  active.json              # which listener is in use
  ian/                     # protected: reset never touches it
    profile.md             # only what the listener said
    guide.md               # the guide's name and manner
    settings.json
    library.jsonl          # works and topics met
    seeds.jsonl            # questions and ideas for future chapters
    up_next.json           # tonight's plan (the card)
    queue.jsonl            # topics set for a later night
    sessions/              # every live conversation: audio + transcript
    chapters/
      2026-11-17-<slug>/   # one folder per chapter: run files, audio,
                           # script, transcript, references, episode.json,
                           # intro_brief.md, status.json, cost.json
  demo-2026-11-20-a/       # created by reset
shared/                    # caches, voices, prompts
```

**settings.json**

| Key | Default | What it does |
| --- | --- | --- |
| `overnight.enabled` | true | Turns the nightly build on or off |
| `overnight.days` | Mon–Fri | Mornings the build runs on |
| `overnight.start` | 02:00 | Build start, also the change cutoff |
| `overnight.plan_at` | 18:00 | When the Up next card is written |
| `overnight.runaway_stop_usd` | 50 | Stops a build that runs away; not a budget |
| `chapter.minutes` | 20 | Target length |
| `chapter.mode` | surprise | Default mode for the next chapter |

**Migration.** Move the existing runs, sessions and profile into `listeners/ian/`. The herons chapter and the first chapter become the first entries in `chapters/`.

**Reset.** Creates a new demo listener and makes it active. It never edits or deletes `ian/`.

## Planning the next chapter

A planning pass at 18:00 on the evening before each build picks tonight's topic and writes the Up next card. It is cheap (scout plus Sonnet) and does no deep research. The overnight build does that.

**Three modes**

| Mode | What the planner does |
| --- | --- |
| Surprise me (default) | Decides for itself whether to go deeper on the last chapter or branch somewhere new. Prefers variety: after three continuations in a row it branches |
| Continue | Builds on the last chapter: its parked ideas, open threads and the questions asked during it |
| Your topic | Uses the topic the listener set by voice (see Change by voice) |

A topic in `queue.jsonl` (set after a cutoff) takes the next night ahead of the mode.

**What the planner reads**

- The last chapter: ledger parked ideas, through-line, listener notes.
- `seeds.jsonl`: questions asked mid-chapter that the chapter didn't answer, and saved seeds.
- `library.jsonl` and `profile.md`: works and interests the listener has mentioned.
- Titles and summaries of every past chapter, so it never repeats one.

**What it writes: `up_next.json`**

- `title`: clear, like a book chapter or podcast episode title. Says what it's about; no clickbait.
- `overview`: one line.
- `summary`: three or four sentences: the question it takes on, the angle, and why it follows from what the listener has heard or said.
- `mode`, `source` (which seed, chapter or request it came from), `research_questions`, `angle`.
- `status`: planned → building → ready (or failed), `locked_at` when the build starts.

The summary is a promise the chapter has to keep. The overnight outline starts from it, and the edit pass checks the chapter against it.

## Change by voice

The Up next card has one button, Change. It opens a live conversation with the guide about what the listener would rather hear. There are no forms or typed fields.

1. Tap Change. The connecting state shows, then the guide speaks first, referring to tonight's plan by its title in a sentence.
2. The conversation can end with a new topic, a different angle on the same one, "surprise me", or "keep it as it is".
3. The guide says aloud what it will make, in a sentence or two, then calls `set_next_chapter(mode, topic, notes)`.
4. The planning pass runs again with the request and rewrites the card. The new card shows within about a minute.
5. The conversation is saved to `sessions/` and goes through the usual after-conversation pass (profile, library, seeds).

**After 2:00.** The build has started and tonight's chapter can't change. The guide says so and saves the request to `queue.jsonl` for the next build day.

**Context the guide gets:** the current card, the last chapter's title and summary, recent seeds, and the listener's profile and library. It does not get research for topics that haven't been researched yet, so it shouldn't promise specifics.

## Overnight runner

One command builds tonight's chapter: `wl overnight run`. A scheduler calls it at 2:00 on build days; you can also run it by hand at any time. The same command is what the cloud machine will run later.

**Stages** (each writes its files to the chapter folder, so a stopped build resumes from the last finished stage)

1. Lock the plan: copy `up_next.json` into the chapter folder, set status to building.
2. Research: the full ledger path, research before the outline.
3. Outline, then write and edit, section by section with the paired edit. No fast-start shortcuts: nobody is waiting.
4. Check the finished script against the card's summary. A chapter that drifted from its promise gets one revision.
5. Record: GPT-Live, server-side, one paragraph at a time. Transcribe each take and compare it with the script; re-record up to 3 times; up to 4 sessions in parallel. These are checks 6 and 10 of the One Live Voice spec.
6. Assemble: stitch the audio, align timings, build `episode.json`, references and the episode page.
7. Write `intro_brief.md` for the morning intro (see Morning playback).
8. Update the library and seeds, mark the card ready, and plan nothing new until 18:00.

**Status and failure**

- `status.json` holds the current stage, its start time, cost so far and any error.
- A failure stops the build and leaves the card showing "Last night's chapter didn't finish", with the reason in the log.
- `wl overnight resume` restarts from the failed stage. If nothing is done, the next build day retries the same plan before planning a new one.
- Reaching the runaway stop counts as a failure.

**Laptop testing.** Use a scheduled job and keep the laptop awake and on power overnight. `wl overnight run --now` is for daytime tests. No laptop-specific code beyond the scheduler entry.

## Morning playback

The listener presses Play on the latest chapter. The guide opens live, talks for under a minute about what it made and what it found, then moves into the chapter on its own. Nothing plays until the listener presses Play.

1. **Connect.** The connecting state shows, then the guide speaks first. The listener's mic is open, so they can answer or ask.
2. **Intro, 30–60 seconds.** Greets them, names the chapter in a sentence, shares one or two things it found that surprised it, and says why this follows from them (something they asked or said). It keeps the intro short if the listener is quiet, and follows them if they talk.
3. **Pivot.** The guide says a natural lead-in line and calls `start_chapter()`. The live session closes and the recording starts from the beginning. Same voice, so the handover should be seamless.
4. **Fallback.** If the guide doesn't call the tool, the page starts the recording after the guide's lead-in line followed by about 1.5 seconds of silence, or at 90 seconds at the latest. Same rule as the ask-by-voice resume.
5. **During the chapter.** Tap to ask, exactly as built: the recording pauses, the live session connects, and the recording resumes with the usual rewind.

**`intro_brief.md`** (written overnight) holds the only things the intro may use:

- The title and the card's summary.
- Two or three findings from the research that are safe to mention: surprising, but not the chapter's main turns.
- The link back to the listener: which seed, question or conversation it came from, in their words.

The guide gets the brief, not the script, so it can't give away what's coming (the same lesson as the ask-by-voice context).

**Recorded vs live.** If you'd rather the chapter be read live after the intro, set `playback.read_live: true`. The intro is the same, and `start_chapter()` switches the session into reading mode instead of closing it.

## Library page

One minimal page with two cards: Up next on top, the latest chapter below. Same look as the rest of the app.

**Up next card**

- Title, one-line overview, summary.
- A small line for when it will be ready ("Ready tomorrow morning") or its state (building, didn't finish).
- One button: Change. Hidden while building; it reappears once the next plan is written.
- After 18:00 on the eve of a non-build day: "Next chapter Monday" and the card for that build.

**Latest chapter card**

- Title, overview, date, length.
- Play (starts the morning flow above) and a link to the episode page.

**Settings** (a small link, not on the cards): overnight building on/off, build days, times. Writes `settings.json`.

Past chapters are stored and get their own list later. For now, the latest chapter is the only one playable.

## Build order, checks and stop rules

Build with Opus 5.5 on branch `feature/overnight`, in this order. Each step is usable before the next starts.

1. **Profiles and storage.** Folder layout, settings, migration of existing files into `listeners/ian/`, reset.
2. **Planner.** The three modes, the queue, `up_next.json`.
3. **Overnight runner.** Stages, resume, status, scheduler, recording with transcript checks.
4. **Library page.** Both cards and settings.
5. **Morning playback.** Intro brief, live intro, pivot, fallback.
6. **Change by voice.** Reuses the onboarding session; last because it's the easiest to slip.

**Acceptance checks**

- [ ] 1\. Existing chapters and sessions open from `listeners/ian/` after migration; nothing lost.
- [ ] 2\. Reset creates a demo listener; `ian/` is byte-for-byte unchanged.
- [ ] 3\. Planner writes a card in each mode from real data; titles read as episode titles; no repeat of a past chapter.
- [ ] 4\. A queued topic takes the next build ahead of the mode.
- [ ] 5\. `wl overnight run --now` produces a complete chapter folder: audio, script, transcript, references, episode page, intro brief.
- [ ] 6\. Recording: every paragraph's transcript matches its script after normalising; report the mismatches left and the re-record count.
- [ ] 7\. The finished chapter is 18–22 minutes and matches its card's summary.
- [ ] 8\. Kill the build mid-research and mid-recording; `resume` finishes it without redoing finished stages.
- [ ] 9\. Overnight off: no build runs. Non-build day: no build runs.
- [ ] 10\. A real scheduled 2:00 build on the laptop finishes by morning.
- [ ] 11\. Change before 2:00 rewrites the card within about a minute; change after 2:00 queues it and the guide says so.
- [ ] 12\. Play: the intro gives nothing from the script beyond the brief; the chapter starts by tool call or fallback every time (10 runs).
- [ ] 13\. Tap during a recorded chapter pauses, answers and resumes with the rewind rules.

Checks 10, 11 (by voice), 12 and 13 need a person; list them in `NEEDS_HUMAN.md` rather than marking them passed.

**Stop rules for unattended runs**

- Commit after each passing check, and keep `PROGRESS.md` current.
- If an API behaves differently from this spec, stop and write down the blocker rather than working around it.
- Log every dollar spent per check in `cost.json`.

## Out of scope

- Playing older chapters, and a list of them.
- A post-chapter conversation.
- Notifications when a chapter is ready.
- Listening signals (finished, skipped, where they asked) feeding the planner.
- The voice bank and voice switching.
- The cloud runner and accounts.
- Weekend builds (the `overnight.days` setting covers it when wanted).
