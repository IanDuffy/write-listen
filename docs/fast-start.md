# Add-on: Fast Start (Section-by-Section Generation)

Build spec for Claude Code. A second way to run the chapter pipeline: the opening plays within a few minutes of the topic being set, and the rest of the chapter is researched, written, edited and narrated section by section while the listener is already listening.

## Goal

- **First audio about 3-4 minutes after the topic is set** (estimate; this build measures it). Today the wait is 20-30 minutes.
- **A 20-minute chapter in 3-5 sections,** each ready before the listener reaches it.
- **Quality held by three things:** a hard edit of the outline before any writing, every section written with the full text of what came before, and a rolling edit that reads two sections at a time.
- **When a section isn't ready,** the audio waits for its edit, and the guide comes in to ask what the listener makes of it so far. What they say shapes the sections still to come.
- **At the end,** the sections are stitched into one archived episode that works with the episode page.

## Two modes

| Mode | Used for | Order of work |
|---|---|---|
| `fast` (new) | The first chapter from onboarding, and anything asked for on the spot | Scout, outline, opening, then research and sections in parallel with playback |
| `overnight` (existing) | Daily chapters | Unchanged: full research first, whole-script edit |

Do not change `overnight`. `start_chapter` from the onboarding guide uses `fast` by default.

Fixed decisions for `fast`:

- No listener approval or editing at any point. Every content decision is the AI's.
- The outline is set before deep research. Accepted.
- No whole-script edit after the chapter. The rolling edit is the edit.
- Audio is held for the edit. Every section is edited before it is narrated.
- The mic opens on a tap, with one exception: the guide's check-in when a section isn't ready.

## What this depends on

- **Onboarding (merged):** voice guide, persona, `start_chapter`, `add_note`, `get_status`, the circle and status line, the local server, the wait conversation and ready ping.
- **Unattended seed pipeline:** scout, angle, ledger, research agents, draft, edit, narrate.
- **Episode page:** player, timing, per-segment audio, `python -m episode build`.
- **Ask by voice:** mid-playback voice sessions, `resume_chapter`, the Connecting state, aside logging.

Branch `feature/fast-start` from the default branch once ask-by-voice is merged. If it is not merged yet, build and test the generation side (milestones 1-4) and stop before milestone 5.

Rules for working alongside the other pieces:

- Read all four codebases first and use the names they actually have. Note differences from this spec in the report.
- New orchestration goes in a new `faststart/` package and `tests/faststart/`. Import and reuse the existing stage functions; do not copy them.
- If a stage function needs a change (for example to accept earlier sections as input), keep it backward compatible and list the change in the report. Onboarding files are still being tuned; keep edits there small.

## The run, in order

| Step | What happens | Target (estimate) |
|---|---|---|
| 0. Early scout | During onboarding, when a likely topic appears, the guide may call `scout_topic` before confirming it | Runs during conversation |
| 1. Scout | Map the territory: sources, key facts, two or three tellable scenes | 60-90 s |
| 2. Outline and outline edit | Angle, through-line, 3-5 sections with notes; a fresh-context editor revises it once | 60-90 s |
| 3. Opening | Write, check and narrate the intro and section 1. Playback starts when the first audio is ready | 60-90 s to first audio |
| 4. Research | One agent per ledger row for sections 2 onward, started the moment the outline is done, in parallel with step 3 | 5-8 min, in background |
| 5. Rolling write and edit | Draft each section; lock each one with the next section's draft beside it; narrate on lock | 2-3 min per section |
| 6. Archive | Stitch the audio, assemble the script, build the episode bundle | After the last section |

Generation always runs as fast as it can, straight through to the end of the chapter. It never waits for the listener.

### Step 0: early scout

- New guide tool `scout_topic(topic)`. The guide may call it silently, at most twice per conversation, when a topic looks likely.
- If the confirmed seed matches a scouted topic, step 1 reuses the result. Otherwise the result is discarded.
- The guide never mentions it.

### Step 1: scout

- Budget: 6 searches, 3 fetches. Same source tiers and quote rule as the main pipeline.
- Output `01_scout.md`: numbered entries with claim, URL and tier, plus two or three concrete scenes or stories with sources. The opening is written from this alone.
- Opus 5.5 by default. If the scout is the slowest step, try Sonnet 5.5, compare the outlines that result, and report.

### Step 2: outline and outline edit

The arc is decided here, because nothing later can reorder the chapter.

One call writes `05_outline.md` and `ledger.json`; a second call with fresh context critiques the arc and returns a revised outline. One round only.

The outline holds:

- The chosen angle and why, the through-line, and the closing idea.
- **3 to 5 sections,** decided by the AI within these limits: about 20 minutes in total (3,000 words at 150 words a minute), the opening at least 6 minutes including the intro, no section under 3 minutes.
- For each section: a short title for its marker, what it lands, what it sets up, how it hands off to the next, target length, and its research questions.
- **The opening must be writable from the scout alone.** Make it story-led: a scene and the question the chapter will answer. It carries few facts, and every one of them comes from a scout entry.
- The listener's own words from the conversation, for the first lines.

Ledger rows come from the sections' research questions, in the existing ledger format, each tagged with its section. Sections 2 onward get 1-2 rows each.

### Step 3: opening

- Write the intro and section 1 from the outline, the scout and the listener's words.
- One edit pass with fresh context: facts against scout entries, quote rule, tone, and that the last lines stand alone and hand off.
- Narrate paragraph by paragraph and make each piece playable as soon as it is done, so playback starts before the whole section is synthesized.
- Word timing: request timestamps from ElevenLabs at generation if the narration stage supports it; otherwise run the aligner on each section in the background. Until timing arrives the page shows the section's text without word highlighting.

### Step 4: research

- One agent per ledger row, section 2's rows first if concurrency is limited.
- Budget: 5 searches and 2 fetches per row; 30 searches for the whole run.
- Each agent returns claims, tellable material, a verbatim quote if one exists, and the strongest objection, as in the ledger add-on.
- No follow-up round in `fast`. A row that comes back thin is flagged and the writer works with what there is.
- Results append to `04_dossier.md` as they land, so ask-by-voice can use them straight away.

### Step 5: rolling write and edit

**Drafting section k.** The writer gets the outline, the dossier entries for its rows, any listener notes (see Listener notes), and **the full text of every earlier section**: the locked text for locked sections and the latest draft for the rest. Use prompt caching on the shared parts.

**Locking section k.** The editor, with fresh context, reads the draft of section k together with the draft of section k+1, plus all locked text, the outline, the ledger and the listener notes. It returns:

- The final text of section k.
- Changes for the draft of section k+1. It may move material between the two.
- Which ledger rows section k landed (covered, thin or missing), written to the ledger.
- Any repetition of an earlier section, removed.

Section k is narrated as soon as it is locked. The last section is locked on its own, with the editor reading the whole chapter so the ending lands the through-line and can call back to the opening.

**Hold audio for the edit.** The paired edit is what gives the chapter its richness, so a section is not narrated until it has had it. If the listener reaches the end of the ready audio first, the check-in below fills the time.

**The threshold.** The page reports the player's position to the server every few seconds. Once the listener has been waiting `max_hold_seconds` (default 180) for a section, stop waiting for the next section's draft: lock the held section with a solo edit, with the editor reading it against all locked text, the outline, the ledger and the listener notes. It is still edited; it only loses the look ahead. Log every hold, its length, and every solo-edit fallback.

**If research contradicts something already played,** the section says so plainly in the guide's voice ("when I looked into this further, I found...") and the correction is logged in `decisions.md`. The editor is asked to watch for it. Build nothing more for this.

### Step 6: archive

When the last section is narrated:

- Stitch the section audio into one chapter file. Check-in conversations are not part of it.
- Assemble the final script and merge the section timings with offsets.
- Run the episode build so the episode page opens the run like any other.
- Conversations during the chapter are kept as text asides at the point they happened, not as audio: questions from ask-by-voice and check-ins, in the same aside log, with a `kind` of `ask` or `check_in`.

Keep the run folder layout the overnight mode uses wherever a file has an equivalent, so the episode page and ask-by-voice need no changes.

## The wait before first audio

The behaviour is already built: the listener can keep talking with the guide, or leave and be pinged. Add to the guide's wait behaviour:

- **Very short replies,** a sentence at most. The aim is to keep the listener talking and get to know them.
- Same tone rules as onboarding: it reacts before it asks, and never asks two questions in a row.
- What it learns goes through `add_note` (see Listener notes).
- The guide gives a time only when the server has supplied one. The server's estimate comes from the medians of earlier runs, stored in `faststart/timings.json` and updated after each run.

## When a section isn't ready: the check-in

Two ways to get there: playback reaches the end of the ready audio, or the listener taps to skip ahead. Skipping moves to the next section marker only; if that section is ready, it plays.

If it isn't ready:

- **A short wait is just a pause.** If the section is expected within about 15 seconds, the circle shows the making state and nothing else happens.
- **Otherwise the guide comes in.** A voice session opens with the ask-by-voice code and its Connecting state, and the guide speaks first, in its own words: what does the listener make of it so far, is anything connecting? It says in a clause that the next part is still coming together. It doesn't apologise or dwell on it.
- **The mic opens with the check-in,** and the circle shows Listening. This is the one place the mic opens without a tap, so that a spoken question can be answered aloud. If the listener says nothing for about 8 seconds, the guide says a word or two, the session closes, and the circle returns to the making state with the button reading "Talk while you wait".
- **Setting:** `gap_mic` is `auto` by default. With `tap`, the guide asks its question and the mic waits for a tap.

**What the guide is told:** the full text played so far, the outline of what is coming (to lead in, never to spoil), the profile, earlier asides in this chapter, and what it asked at any earlier check-in, so it doesn't open the same way twice.

**How it behaves**

- Very short replies. It reacts, then lets the listener talk. Same tone rules as onboarding.
- It listens for what landed, what was unclear, what the listener tied to their own life, and what they want more of, and saves each with `add_note`.
- Questions are answered as in ask-by-voice.

**When the section is ready**

- With a conversation open, the guide is told. It lets the listener finish, says a lead-in in its own words that ties what they said to what is coming, and calls `resume_chapter`. Confirm in ElevenLabs' current docs how to send an update into a live session; if that isn't supported, the guide calls `get_status` after each listener turn.
- With no conversation open, playback starts after a short beat.
- A conversation is never cut off. The section waits for it.

**Between sections when the next one is ready:** a one-second pause, then it continues. No check-in.

## Listener notes

Everything the listener says after the topic is set can shape what hasn't been locked yet: the wait before first audio, check-ins, and questions asked mid-chapter.

- One file per run, `listener_notes.md`, appended by `add_note` and by the ask-by-voice aside log. Each note has the time, the section it was said in, and a kind: landed, unclear, connection, wants more, or question.
- The writer and the editor of every section not yet locked receive the notes.
- **Notes may change:** emphasis, the choice of example, a callback to something the listener said, a line that clears up what was unclear, and an answer to a question when the dossier has it.
- **Notes may not change:** the section list, the through-line, or anything already locked.
- A note the dossier can't serve may start one extra research agent (3 searches, 1 fetch) for a section whose drafting hasn't begun. At most two per chapter.
- Notes also go to the profile, as they do now.

## On the page

- **Section markers:** the section titles from the outline, each in one of four states: played, playing, ready, being written. A title may change when its section locks.
- Tapping a ready or played marker jumps to it. Tapping one that is being written does what skipping does.
- `get_status` and `status.json` report each section's state and the estimate.
- Same minimal look as the rest. No new controls beyond the markers.

## Logs

- `fast_timeline.jsonl`: a timestamped line for every event (seed set, scout done, outline done, first audio ready, each draft, lock and narration, each hold and check-in with its length, every solo-edit fallback).
- `decisions.md`: the angle chosen and why, the outline editor's changes, material moved between sections, thin rows, corrections.
- Cost per call, as elsewhere.

## Acceptance checks

Automated, with recorded model and speech responses:

1. Outline validator: 3-5 sections, opening at least 6 minutes, none under 3, total 18-24 minutes; every section has its lands, sets-up and hand-off notes; the opening cites only scout entries.
2. Every draft call includes the full text of all earlier sections.
3. Scheduler simulation with made-up step durations, covering uninterrupted listening, a skip early in section 1, a long pause, and slow research: nothing is narrated before it is edited, order is kept, audio is held for the paired edit, and the solo-edit fallback fires only after `max_hold_seconds` of waiting.
4. The lock step can move material between two sections without losing or duplicating text.
5. Check-in flow: a wait under 15 seconds brings no check-in; a longer one opens the session with the guide speaking first; silence closes the mic after about 8 seconds; `resume_chapter` is called after the ready signal and never while the listener is speaking; with `gap_mic: tap` the mic stays closed until the tap.
5b. Notes saved during a check-in or a mid-chapter question appear in the inputs of every section drafted or locked afterwards, and in none already locked.
6. Skipping goes to the next marker only.
7. Archive: stitched length equals the sum of the sections; merged timings never go backwards; the episode page loads the run; asides appear as text in the right places.
8. Every quote in locked text matches a verbatim entry; every dossier reference exists.

End to end, with real calls and a simulated player that plays in real time:

9. Three runs on three different typed seeds. For each, report the timeline: seed to scout, outline, first audio, and for every later section when it was ready against when playback needed it. Report every hold and its length, and whether a check-in would have covered it.
10. One run with a skip two minutes into section 1, to force a check-in. Script the listener's side with three remarks: something that landed, something unclear, and a personal connection. Report how the later sections used them, quoting the lines.
11. For one seed, also run `overnight`. Have a separate model pass with fresh context compare the two scripts blind on arc, repetition and accuracy against the dossier. Report the verdict and quote the differences that matter.
12. Report the ledger coverage for each fast run, how many holds there were and how long, and how many sections fell back to a solo edit.
13. Leave Ian a short script for a live try: one onboarding conversation by voice through to the end of the chapter, with one question asked mid-chapter and one skip to bring on a check-in.

Spending cap for the build: $25, logged per call. Stop and report if it is reached.

## Milestones

1. Scout, outline and outline edit, with the validator.
2. Opening: write, check, narrate in pieces, first audio playable.
3. Parallel research and the rolling write and edit, driven by a simulated player.
4. Archive, and the end-to-end runs in checks 9-12.
5. Page and guide: section markers, the check-in, listener notes, the ready signal, wait behaviour, `scout_topic`.

Work through the build without stopping for review. Fix and re-run any failing check. Stop only for the spending cap, or if the existing pieces can't be made to work together; report what conflicts.

## Deliverables

- `faststart/` package and tests; the page and guide changes in milestone 5.
- `faststart/REPORT.md`: what was built, results for every check, the timeline tables, the fast-versus-overnight comparison, total cost, every change made to existing files and why, and Ian's live script.

## Out of scope

- A whole-script edit after the chapter, or a polished second version.
- Research before the outline in `fast`.
- Any change to `overnight`.
- Detecting contradictions beyond the one rule above.
- Skipping further than the next section.
- A check-in when the next section is already ready.
- Images, accounts, more than one listener.
