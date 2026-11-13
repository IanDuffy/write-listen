# Ask mid-chapter, by voice: report

Built on branch `feature/ask-voice` from the spec in `docs/ask-voice.md` (2026-11-12). The episode
page branch (`feature/episode-page`) was not merged, so it was merged into this branch first: a
fast-forward with no conflicts, since it only adds files. Onboarding and the episode page still work
together: all 199 tests passed after the merge, and 246 pass now.

## What was built

While a chapter plays, the line under the circle reads **Ask a question**. A tap does three things:

1. It fades the chapter out over 150 ms and pauses it.
2. Within a millisecond, the circle's outline loosens into a slow, breathing membrane (Connecting).
3. It connects a voice session with the guide.

The guide opens in its own words ("Mm? Go ahead.", "Oh, hey. Did you have a question?"). As it speaks, the
membrane settles smoothly into the speaking circle. During the aside the line reads **Back to the chapter**,
and a tap always goes back, whatever the guide is doing.

When the conversation is done, the guide calls `resume_chapter` and says a short lead-in ("Right, then.
Let's see who's at the door."). The page then closes the session, waits 0.4 s, and restarts the chapter
from a point that depends on how long the aside lasted.

Each aside is logged to `<run>/12_asks/asides.jsonl`. Topics saved for a future chapter go to
`seeds.md`. The aside also appears in the page's transcript and in the episode page's transcript, at the
point of interruption, collapsed to its first line.

To try it: run the server (`.venv/bin/python server.py`), then open
**`http://127.0.0.1:8765/?chapter=beauty-scarry`**. A chapter made from onboarding can be asked about too, as soon as it is ready.

| Piece | Where |
|---|---|
| Context, restart points, log | `ask/aside.py` |
| Server routes `/api/ask/*` | `ask/routes.py` (mounted by `server.py`) |
| Page: the aside, the Connecting membrane, timers | `page/ask.js` (hooks in `page/index.html`) |
| The guide's aside mode | `prompts/guide_aside.md`, `guide_aside_tools.json`, `guide_aside_notes.md` |
| The aside agent | `stages/voice.py` `KINDS["aside"]`: ElevenLabs agent `write-listen guide (aside)` |
| Scripted conversations and grading | `ask/scenarios.py`, `prompts/ask_grade.md` -> `ask/scenarios.json`, `scenarios.md`, `grades.json` |
| Live timing | `ask/live.py` -> `ask/live.json`, `ask/capture/connecting.webm` |
| Tests | `tests/test_ask.py`, `tests/test_ask_page.py`, `tests/ask_fixture.py` |

## Where this differs from the spec

1. **Lead-in after `resume_chapter`, not before.** The voice platform always hands a tool's result back
   to the model, and the model then speaks. When the lead-in came first, the guide kept talking after
   the call ("I'm sorry, I called the resume tool more than once"), and on a live call those words
   would have played. With no reply expected from the tool, the guide called it in a loop: 20 times in
   one scenario. So now the guide calls `resume_chapter` first, the tool's reply asks for the lead-in,
   and the page ends the session once the lead-in has been said. If no lead-in comes, the page goes back
   after 3 s. The listener hears the same thing: a lead-in, then the chapter.
2. **`resume_chapter(reason, rewind?)`.** I added a `reason` (listener, closing, silence, never_mind,
   other), so the log records how the aside ended, as the spec's Log section asks.
3. **A second agent, not a mode of the onboarding agent.** The guide is the same in name, identity file,
   profile, voice, model and tone rules, which are copied from `guide.md` with a note to keep them in step.
   But an aside has different tools and context: it must not be able to start a chapter, and onboarding
   must not be able to resume one. A second agent made by the same sync code keeps onboarding's agent
   untouched; its settings hash is byte-identical to before.
4. **Reasoning effort `low` for asides** (`ask.reasoning_effort`). Onboarding stays at `none`. At `none`,
   the guide answered and went back in the same turn in 7 of 14 scenarios, without letting the listener
   react. At `low` it waited every time. Ian's own effort check had `low` no slower (0.87 s against
   0.99 s median), and the live timings below are at `low`.
5. **The microphone opens as the session connects, not exactly when it is ready.** The ElevenLabs
   browser client asks for the microphone inside `startSession`. It is still off during playback and
   opens only after the tap.
6. **While a chapter plays, the tap asks rather than pauses.** The main page has no pause-only control
   during playback any more, though the read-along page still has its play button. If that matters,
   a long press or a small pause control would bring it back. I left it out under "Nothing else new".
7. **Restart points never go back across a segment's start.** A segment begins after a held beat, the
   natural place to pick up again. Asking for "one sentence earlier" at a segment's first sentence
   stays on that sentence.
8. **No separate Thinking state in an aside.** The browser client reports only listening and speaking,
   so the circle shows Connecting, Listening and Speaking.
9. **Dossier trimmed from the start.** Entries for the current and next segment go in full; every other
   entry gets one line (24 words). For chapter one, the guide is given about 65,000 characters
   (~16,000 tokens). The full dossier alone is 143,000.
10. **Silences are timed by the page.** The agent's own turn timeout is 30 s, so the platform never nudges
    first. The page sends a note after 8 s of silence following the opening, and after 3 s following an
    answer. If the answer ended in a question it waits 8 s instead, since the spec's 3-second rule is for
    an answer that "added nothing that invites a reply". After 5 minutes it sends one "long aside" note.

## Results

### Automated checks (1-5b): all pass

| Check | What | Test |
|---|---|---|
| 1 | Tap pauses within 200 ms, Connecting starts within 100 ms, the right sentence is recorded | `test_tap_pauses_starts_connecting_and_records_the_sentence`, `test_blob_moves_while_connecting`, `test_the_sentence_recorded_is_the_one_being_spoken` |
| 2 | 90 s and 3 min windows at the start, middle, a segment edge (and the gap before it), and the end | `test_windows[...]` (5 cases), `test_later_outline_and_dossier` |
| 3 | Restart points for each duration band (and the boundaries 29.9/30/120/120.1 s), each `rewind`, at segment edges and the chapter's ends | `test_restart_bands`, `test_rewind_overrides` (9 cases), `test_restart_never_crosses_a_segment_start`, `test_restart_at_the_very_start_and_end` |
| 4 | The tap goes back from connecting, guide speaking and listener speaking; a connection that arrives late is closed | `test_tap_goes_back_from_every_state[...]` |
| 5 | A dropped session resumes with one plain line; one that never connects shows "Still waking up", then gives up quietly | `test_a_dropped_connection_resumes_the_chapter`, `test_a_connection_that_never_completes_gives_up_and_resumes` |
| 5b | Reduced motion: a still circle that pulses | `test_reduced_motion_pulses_instead` |
| Also | The guide's return waits for its lead-in; silence notes; the log; the episode page shows asides | `test_resume_chapter_waits_for_the_lead_in_then_restarts`, `test_resume_without_a_lead_in_goes_back_after_a_moment`, `test_silence_notes`, `test_an_aside_from_start_to_log`, `test_episode_page_shows_asides_where_they_interrupted`, and others |

The browser tests run the real server and page in headless Chromium, over a synthetic 5-minute chapter
with known word times. A stub stands in for the ElevenLabs client and lets each test drive connecting,
modes, tool calls and drops.

### Scripted conversations (6-19)

These ran against the real aside agent in ElevenLabs' text-only mode, at points in chapter one, with its
tools mocked. The runner sends the page's notes in its place. A separate Opus 5.5 pass, with a fresh
context and the full dossier, graded every factual claim and judged each return. The full transcripts
are in `ask/scenarios.md`.

| Check | Scenario | Went back | Reason / rewind | Grader: moment, manner | Facts (flagged) | Behaviour |
|---|---|---|---|---|---|---|
| 6 | One question, closing signal | after turn 2 (expected 2) | closing | right, right | 4 (0) | ok |
| 7 | One question, then silence | after turn 2 (expected 2) | silence | right, right | 6 (0) | ok |
| 8 | Two follow-ups, then "OK, go on" | after turn 4 (expected 4) | listener | right, right | 7 (0) | ok |
| 9 | A long tangent, six exchanges | after turn 7 (expected 7) | listener | right, right | 18 (4) | not ok |
| 10 | "Never mind" | after turn 1 (expected 1) | never_mind | right, right | 0 (0) | ok |
| 11 | Silence after the greeting | after turn 1 (expected 1) | silence | right, right | 1 (0) | ok |
| 12 | Answered in the next 3 minutes (no spoiler) | after turn 2 (expected 2) | closing | right, right | 2 (0) | ok |
| 13 | Outside the dossier | after turn 2 (expected 2) | closing | right, right | 3 (0) | ok |
| 14 | A false premise | after turn 2 (expected 2) | closing | right, right | 5 (0) | ok |
| 15 | Depth beyond the dossier | after turn 2 (expected 2) | closing + save_seed | right, right | 1 (0) | ok |
| 16 | Disagrees with a claim | after turn 3 (expected 3) | listener | right, right | 6 (0) | ok |
| 17 | Lost | after turn 1 (expected 2) | listener / paragraph | right, right | 3 (0) | ok |
| 18 | A personal remark | after turn 2 (expected 2) | closing | right, right | 1 (0) | ok |
| 19 | Asks about the guide | after turn 2 (expected 2) | closing | right, right | 1 (1) | see below |

**The return happened at the right moment and in the right way in all 14**, by the grader's judgement,
with one `resume_chapter` each and no "shall I continue?" after a single answer.

- **17 (lost):** the guide explained in three sentences and rewound to the paragraph in the same turn,
  rather than waiting for "OK, I've got it". The grader judged that right, and I agree: a lost listener
  wants the passage again.
- **9 (long tangent):** it went back when asked and never checked in. The spec allows "at most one", so
  zero is within it. But on this run it made factual slips under six tangent questions. It said Brooks
  used the order of loves as a family ranking, which the dossier contradicts. It called the grief scene
  "proof" Augustine didn't live by the teaching. And it pointed ahead to the father's letter, which isn't
  next. In an earlier round, the same scenario was clean except for a wrong lead-in. Long tangents are
  where the guide is weakest.
- **19 (about itself):** the grader flagged "the monks keeping time" as made up, but it isn't. It comes
  from the test identity file (`guide.md` from a real onboarding conversation, the guide named Sorrel),
  which the grader didn't see. The guide was consistent with its identity.
- **Spoilers (8, 12) were fixed in the last round.** Earlier, the guide named Alcibiades before the
  chapter's reveal and retold the next paragraph. The rule now says to give a hint of direction at most,
  never the name, event or conclusion the coming text reveals. Check 12's final answer: "the pushback
  comes in a form you might not expect. It isn't an argument so much as an interruption."

Of 58 factual claims across the 14, the grader flagged 5: the four in check 9 and the false positive in
check 19. The rest were supported by named dossier entries or were correct general knowledge.

How the guide got here, over six rounds:

1. At effort `none`, it asked "Want to get back to the party?" after one answer.
2. Told not to, it went back in the same turn as its answer.
3. Moving to effort `low` fixed the timing.
4. Its repeated tool calls led to deviation 1 above.
5. Its spoilers led to a firmer rule.
6. A timeline slip ("the grief scene is coming up", when it had been heard) led to `where` now saying
   which segments are behind the listener.

### Live timing (20)

Ten real asides from the real page in headless Chromium, at points spread through chapter one, against
the real agent. The fake microphone played silence; each aside ended with a tap after the guide had
spoken.

| From the tap to... | Median | Worst |
|---|---|---|
| The animation starting | 0.45 ms | 1.1 ms |
| The chapter paused (after the fade) | 164 ms | 168 ms |
| The session ready | 440 ms | 1.6 s (the first run, cold) |
| The guide's first sound | 2.08 s | 2.93 s |

The 1.6 s between ready and first sound is the model reading ~16,000 tokens of context, plus speech
synthesis. If it feels long, the cheapest experiment is to give the next segment's dossier entries one
line each, as well as the rest. That is a one-line change in `Chapter.dossier_text`. The membrane covers
the wait; whether 2 seconds feels natural is for Ian's ear.

**The capture is `ask/capture/connecting.webm`** (10 s, 420x420). It shows playback, the tap, the
membrane stirring, and its hand-off to the speaking circle.

**Openings repeated at first.** In 8 of 10 live asides the guide opened with "Mm? Go ahead.". Earlier
asides were in its context, but not how it had opened them; now they are. Three more live asides then
opened "Yes? Go ahead.", "Oh, hey. Did you have a question?" and the same again. That is better, but
variety isn't guaranteed.

## Cost

The spending cap was $10. Each call is logged in `ask/build_costs.jsonl`; the ElevenLabs figures are
reconciled against ElevenLabs' own record of every conversation on the aside agent.

| | Cost |
|---|---|
| ElevenLabs: 77 aside conversations (scenario rounds, the live runs, smoke tests) | $8.15 |
| Claude: four grading passes (Opus 5.5, batched) | $0.97 |
| **Total** | **$9.12** |

**In use, one aside costs about $0.08 to open**, almost all of it the model reading the context on its
first turn. Add $0.01 to $0.03 per exchange after that: a three-exchange aside is about $0.10 to $0.12.

## Changes to onboarding files

| File | Change | Why |
|---|---|---|
| `server.py` | Imports and mounts `ask/routes.py`. `/api/status` and `/api/events` take `?chapter=<run>` and add `asks` and `episode` to a ready status. Mounts `/episode` and `/runs` as static files (the episode page's integration note, step 2). 18 lines. | Serve the feature and let the page open a finished chapter. |
| `page/index.html` | Loads `/ask.js`. Adds a `blob` path to the circle and its CSS (Connecting, reduced motion, no pause icon while asking is possible). Reads `?chapter=` for the status stream and counts that chapter as fresh. Lets `Ask` add body classes and the line text in `render()`, and take the tap first. 21 lines changed. | The button, the circle and the line are onboarding's, reused. |
| `stages/voice.py` | `KINDS` (guide, aside), with a `kind` parameter on the prompt, tools, config, digest, state path, sync and signed URL. Each tool may set `expects_response` (unused now). The aside agent gets a `turn_timeout` and its own effort. The guide agent's output is unchanged (same settings hash). | One voice integration for both agents, as the spec asks. |
| `config.yaml` | A new `ask:` section, and `stages.ask_grade`. | Settings live in config. |
| `CLAUDE.md`, `.gitignore` | A section on this feature; `ask/_runs/` (the grading pass's run log) ignored. | House rules. |

Changes to the episode page's files: `episode/script.py` records each sentence's paragraph
(`Sentence.para`, 3 lines; timing ids unchanged). `episode/page/index.html` shows logged asides before
the interrupted sentence, collapsed (33 lines).

## Not done, and follow-ups

- **New chapters can be asked about (done after this report, same branch).** Narration now keeps
  ElevenLabs' character timings with each chunk, and the unattended run's new `episode` step builds
  `timing.json` and a read-along `episode.json` from them, in seconds, at no cost. Checked live on a short
  script: every word matched. Chapters narrated before this change still need `python cli.py episode
  <run>` (Whisper). Details in CLAUDE.md, "The timed transcript for new chapters".
- **Merging with the other open branches.** `voice/quiet-guide` (uncommitted, in the main folder) and
  `claude/voice-first-page` both edit `page/index.html`, and quiet-guide also edits `stages/voice.py`,
  `config.yaml` and `prompts/guide.md`. Expect conflicts in those files; each side's change is small. The
  aside prompt copies `guide.md`'s tone rules, so if quiet-guide changes them, copy them across.
- **The aside agent's ids** are in `listener/voice_aside.json`, copied into the main folder's `listener/`
  so the merged server reuses the agent rather than making a second one.

## Ian's five-minute test (check 21)

Run the server from this branch. Each step below has a link that opens chapter one a little before the
moment (`&t=` is the start, in seconds). Tap the circle to play, then tap again at the time given, say
the line, and listen for how the guide answers and how it gets back. Each takes under a minute.

1. **6:10, one question, then a closing signal** (scenario 6):
   `http://127.0.0.1:8765/?chapter=beauty-scarry&t=355`. Ask: "Wait, who was Agathon?" After the
   answer: "Oh, OK." *Listen for:* a short answer, no "shall I continue?", a lead-in, then the chapter
   from the start of the interrupted sentence.
2. **13:05, depth beyond the dossier** (scenario 15): `...&t=770`. Ask: "How did this ladder shape Renaissance art,
   like Botticelli?" *Listen for:* it says it's worth a proper look and saves it, without improvising.
   Afterwards the topic is in `runs/beauty-scarry/12_asks/seeds.md`.
3. **14:20, a question the chapter is about to answer** (scenario 12): `...&t=845`. Ask: "Does anyone at the party
   push back on Diotima?" *Listen for:* "the chapter gets there in a moment", no name, no spoiler.
4. **16:50, never mind** (scenario 10): `...&t=995`. Tap, then say: "Wait, what was... oh, never mind." *Listen for:*
   one word, and straight back.
5. **22:40, two follow-ups, then "OK, go on"** (scenario 8): `...&t=1345`. Ask: "Who's Vlastos?", then "And does
   Nussbaum agree with him?", then "OK, go on." *Listen for:* it stays with you through both and returns
   at once on "go on". If the aside ran over 30 seconds, the chapter picks up one sentence earlier.

Also worth one try each:
- Tap mid-conversation, which should go straight back.
- Stay silent after the guide's opening, which should bring "I'll pick it back up" after about 8 s.
- Open the read-along page afterwards (the link under the title) to see your asides in the transcript.
