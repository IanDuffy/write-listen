# Audio Chapters: Build Phases and Architecture

Nov 11, 2026 · @Ian Duffy

## Phase 1: first meeting to first chapter

Phase 1 is the onboarding conversation, built so that it ends in a real chapter made with no human checkpoints. A listener opens one page, talks with the guide for a few minutes, and about half an hour later hears a chapter made from that conversation.

It goes first for three reasons:

- **It is the demo.** The first two minutes decide whether someone leans in.
- **It forces the unattended run.** A conversation can't stop for Ian to approve an angle, a ledger and a brief, so the pipeline has to run on its own. That was already the next experiment.
- **It exposes the one real gap.** Chapter one started from a transcript. A conversation gives only a seed ("I can't stop thinking about X"), so the pipeline needs a new front end that turns a seed into an angle and finds its own sources.

**The condition:** the conversation must be wired to the pipeline from the start. A charming conversation that leads nowhere proves nothing about the product.

Built for one listener (Ian) on one machine. No accounts, profiles or demo mode yet.

**Rules for Phase 1**

- **The listener never approves, edits or uploads anything.** Every content decision is the AI's. Anything the listener has to do is a reason to leave.
- **If the guide is unsure, it may ask one question to narrow the scope.** Otherwise it decides.
- **Sources come from the open web.** Listener-supplied material is a later-phase fallback.
- **A 20-minute lesson:** about 3,000 words, 4 segments, 3-4 ledger ideas.
- **Laptop only.**
- `decisions.md` is a developer log for Ian to review afterwards. The listener never sees it.

## Architecture

&#91;embedded content: Phase 1 architecture · 6 parts\]

The browser page is the only thing the listener touches. It carries live audio to the voice guide and passes the guide's tool calls to a small server on the laptop, which starts the existing chapter pipeline and reports its progress back. The pipeline is the one part already built.

## Components and choices

| Part | Use | Why | Alternative, if it falls short |
| --- | --- | --- | --- |
| Real-time voice | ElevenLabs Agents | Same voice library as the narration, so the guide and the chapters share a voice. Handles listening, turn-taking and being interrupted. Lets Claude write the replies | Pipecat or LiveKit Agents (open source) with separate speech-to-text, Claude and ElevenLabs speech. More control, much more to build |
| Conversation model | A fast Claude model (Haiku 5.5 or Sonnet 5.5, whichever the platform offers) | In live speech, response time matters more than depth | Custom-model option pointing at the Claude API directly |
| Tools the guide can call | `start_chapter`, `add_note`, `get_status`, as client tools that run in the page | The page calls the local server, so nothing on Ian's machine has to be exposed to the internet | Server tools through a tunnel |
| Local server | FastAPI, one small file | Serves the page, starts pipeline runs, streams status | None needed |
| Memory | Plain files in a `listener/` folder | Readable and hand-editable; one listener for now | SQLite, when accounts arrive in Phase 4 |
| After-conversation pass | Claude Opus 5.5 | Careful reading of the transcript to write the seed and update the profile | Sonnet 5.5 if cost matters |
| Chapter pipeline | The existing CLI, with a seed entry point and an `--unattended` flag | Already works; only the front end and the checkpoints change | None |
| Page | One HTML file, plain JavaScript | Minimal by design; real front-end work comes later | None |

**Two things for Claude Code to confirm in ElevenLabs' current docs before building:** that client tools and per-session prompt overrides work as described here, and which Claude models the platform offers.

**Voice match:** the guide uses the same voice as the chapters, but live conversation runs on a faster speech model than narration. Expect it to sound like the same person, slightly less polished.

## How a first session runs

1. Ian opens the page and taps once to start (browsers need a tap before using the mic).
2. The page starts a voice session and passes in the listener profile, if one exists.
3. The guide opens in the tone set out in the ideas doc: arrival, curiosity both ways, short.
4. After a few exchanges the guide has a seed. It says aloud what it is going off to research and make, the way a good assistant confirms a task before starting. A yes, or no objection, and it goes; if the listener steers, it adjusts. Nothing to click or review.
5. The guide calls `start_chapter` with the seed. The page passes it to the local server, which starts an unattended pipeline run.
6. The guide says what's happening and how long it will take, and offers to keep talking or to ping when it's ready.
7. The indicator shows the current stage. Anything else the listener says that bears on the chapter goes in through `add_note`.
8. When the chapter is ready, the page shows a play control and sends a browser notification.
9. The chapter opens by tying back to the conversation.
10. After the session, the transcript is saved and the profile is updated.

**The wait in Phase 1 is roughly 20 minutes for a 20-minute lesson** (estimate; the log will give the real figure). The guide's line should say so. Streaming generation in Phase 3 brings first audio down to about 10 minutes.

### Finding the seed without an interview

This is the hardest design problem in Phase 1. Most people arrive with nothing in mind, and questions in a row feel like a form.

- **Share first, then ask.** The guide offers something of its own, a small thing it finds delightful, before it asks anything. People answer an offer more freely than a question.
- **Never two questions in a row.** Between questions come a reaction, a connection or a short thought.
- **Ordinary answers are doors.** Whatever the listener mentions (their work, a trip, a show, what they did today) has something remarkable behind it. Someone who pours concrete gets Roman harbors that are still standing. The lesson comes from what sits next to their life, not from a stated interest.
- **Listen for energy, not answers.** Where they talk longest, laugh, go off on a tangent, or ask the guide something back.
- **Offer tastes.** If nothing emerges, the guide floats two or three 15-second teasers on different subjects and follows the one that gets a reaction.
- **It decides.** The guide ends by saying what it is going to make, as a gift. If it is torn between two directions it may ask one narrowing question. If the listener gives it very little, it picks and says it wants to surprise them.
- **Signal in Phase 1 is the transcript:** what was said and how much. Tone of voice as a signal comes later, if the voice platform exposes it.

These rules go into the guide's persona prompt in milestone 3, where they can be tuned as typed conversations before voice is added.

### The guide's identity

Each listener gets their own guide. It is not one shared assistant with the same name for everyone.

- **It names itself** in the first conversation, in the moment, and can say why it chose the name.
- **Keeping names different.** Language models left to choose freely tend to land on the same few names. The server hands the guide a small random draw from a long list; it picks from those, or picks its own if the conversation suggests one.
- **It stays consistent.** `listener/guide.md` holds its name, how it chose it, the date they met, and what it has said about itself.
- **Uniqueness comes from shared history:** the name, the lessons made together, the references that build up between them. Not from a randomized personality.
- **Voice:** one voice in Phase 1. A voice per guide, used for both the conversation and that listener's lessons, is a Phase 4 option.

### What changes in the pipeline

- **Seed entry.** A new first stage takes `seed.md` (the topic in the listener's words, plus what they said about why) in place of a transcript.
- **Scout.** A short research pass, capped at 10 searches, maps the territory and finds candidate sources. It plays the role the episode map played in chapter one.
- **Auto-chosen angle.** The pipeline proposes 3-4 angles and picks one using the seed and the profile, recording why.
- **`--unattended`.** Ledger, brief and script checkpoints are approved automatically. Every automatic decision is written to `decisions.md` so Ian can review what it chose afterwards.
- **Cost cap.** A per-run spending limit; the run stops and reports if it would exceed it.
- **Opening callback.** The brief gets one more input: what the listener said, for the chapter's first lines.

## Memory store

Plain files, one folder, readable by Ian at any time.

| File | Holds | Written |
| --- | --- | --- |
| `listener/profile.md` | What the listener has said about themselves and their interests, in their own words. Short: under a page | Updated after each conversation |
| `listener/guide.md` | The guide's own identity for this listener: its name, how it chose it, when they met, what it has said about itself | First conversation; updated when it says something new about itself |
| `listener/sessions/<date>.jsonl` | Full transcript of each conversation | During the session |
| `listener/seeds/<run>.md` | The seed for each chapter: topic, why it matters to the listener, anything to include or avoid | When `start_chapter` is called |
| `listener/history.md` | Chapters made so far: title, angle, the ledger's parked ideas | After each run |

Rules for the profile update pass:

- Record only what the listener said. No guesses about personality, mood or motives.
- Keep it small. Merge and replace lines; don't append forever.
- The listener can ask the guide what it remembers, and can ask it to forget something. Both must work.

The profile and the last few `history.md` entries are passed into the guide at the start of each session, which is what makes a returning conversation different from a first one.

Later signals (questions asked mid-chapter, chapters finished or dropped) join this in Phase 2, in a `signals.jsonl` file.

## Status indicator and minimal UI

One page, one element in the middle of it, and a line of text under it.

| State | Shows | Text under it |
| --- | --- | --- |
| Idle | Still circle | "Tap to begin" |
| Listening | Circle responds to the mic level | None |
| Thinking | Slow pulse | None |
| Speaking | Circle responds to the guide's voice | None |
| Making the chapter | Thin progress ring around the circle | Stage in plain words and time left: "Researching · about 12 min" |
| Ready | Play control | Chapter title |
| Problem | Circle dims | One plain sentence and a retry control |

- **Where progress comes from:** the pipeline writes `status.json` after every stage (stage name, plain-language label, stages done, estimated minutes left). The server streams it to the page.
- **Spoken status:** the guide can call `get_status` and say it aloud, so the listener never has to look.
- **Also on the page:** a text box as a fallback for typing, and a toggle to show the live transcript. Nothing else.
- **Style:** no framework, no design system, system font, one accent color. Front-end work waits for a later phase.

## Phase 1 milestones

Build in this order. Each one is usable on its own, and the first is the gate for the rest.

- [ ] **1. Seed to chapter, typed.** `python cli.py new --seed "..." --unattended` produces a finished chapter with no checkpoints. Ian listens. If it isn't good, fix this before anything else.
- [ ] **2. Status and page.** `status.json`, the local server, and the page with the indicator and player. A run can be started from the text box.
- [ ] **3. The conversation in text.** The guide's persona prompt and its three tools, tested as a typed chat. Cheap to iterate on tone.
- [ ] **4. Voice.** The ElevenLabs agent with the chapter voice, client tools and the profile passed in.
- [ ] **5. Memory.** Transcripts saved, profile update pass, and a different opening for a returning listener.
- [ ] **6. End to end with a second person.** Someone other than Ian has the first conversation and hears their chapter.

Milestone 1 can start now; it needs no new services.

## Later phases

| Phase | What it adds | Starts when |
| --- | --- | --- |
| 2. Player and interruption | Episode page with synced transcript and references; per-segment audio; ask a question mid-chapter and get an answer from the dossier in the same voice | Unattended chapters hold up across 3-5 different seeds |
| 3. Streaming and the daily lesson | Segment-by-segment generation so first audio arrives in 10-15 minutes; a nightly run; a curriculum memory of what's been covered, parked and asked | Interruption works, and the real cost per chapter is known |
| 4. Accounts and sharing | Multiple listeners, profiles, a demo mode, hosted deployment, phone use, shareable episode pages, a database; a link or upload from the listener as a fallback when the guide can't reach a source | Someone other than Ian has used it for two weeks |

Details for each are in the ideas doc; each gets its own architecture section here when it becomes the current phase.

## Risks, costs and open questions

**Risks**

- **Unattended quality from a seed is unproven.** Chapter one had a rich transcript and four human checkpoints. This is the biggest risk, which is why it is milestone 1.
- **The 20-minute wait.** Fine for Ian; awkward in a live demo. For demos before Phase 3, start the conversation, then play a chapter made earlier from the same seed.
- **Platform dependence.** The voice layer runs on ElevenLabs' hosted agent platform. The persona prompt and tools live in this repo, so moving to an open-source voice stack later is contained.
- **Recording.** Conversations are transcribed and stored. Anyone other than Ian who tries it should be told first.

**Costs (estimates)**

- Voice conversation: about $0.08-0.10 per minute, with the model billed on top on some plans. A 10-minute first meeting is about $1. The free tier is reported to include 15 minutes a month, enough for early testing. [Pricing as read October 2026](https://www.developersdigest.tech/voice-agents/elevenlabs-agents)
- Each 20-minute lesson: roughly half of what the log shows for chapter one, plus the scout pass.

**Open questions for Ian**

- None open. Decided: the guide chooses its own name in the first conversation, and each listener's guide is their own.
