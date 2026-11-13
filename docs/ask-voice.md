# Add-on: Ask Mid-Chapter, by Voice

Build spec for Claude Code. **Replaces `ASK_MID_CHAPTER_SPEC.md`.** The listener taps a button while a chapter plays, talks with the same real-time voice guide they met in onboarding, and the chapter picks up again when the conversation is done.

## Goal

1. The listener taps the button during a chapter. The audio pauses at once.
2. The guide answers within a moment: "Yes?" or "Oh hey, did you have a question?"
3. They talk, for as long as feels natural. The guide knows where the chapter is, what was just said and what is coming.
4. The guide leads back in ("OK, where were we... oh, right") and the chapter resumes.

It is one guide throughout: the same name, voice and manner as onboarding.

## What this depends on

This is the first piece that needs the others joined up. It uses:

- **The onboarding work (merged):** the ElevenLabs voice agent and its session code, the guide's persona and identity files, the page's main button and circle indicator, the local server. Reuse this code; do not write a second voice integration.
- **The episode page work:** the player, `timing.json`, and `nearest_sentence_boundary`.

Onboarding is on the default branch and may still be getting small tweaks. Check whether the episode page is merged too; if not, merge it into a new branch `feature/ask-voice` from the default branch and confirm onboarding and the episode page each still work. Read both codebases first and use the names they actually have; note differences from this spec in the report. Keep changes to onboarding files small and list each one, since that code is still being tuned.

## The button

Reuse the onboarding button. While a chapter plays it reads **"Ask a question"**. During a conversation it reads **"Back to the chapter"**. It is a toggle and always works: tapping it mid-conversation ends the conversation and resumes, whatever the guide is doing.

The mic is off during playback. It opens only on the tap.

## Starting the conversation

- **On tap:** pause the audio immediately (fade over about 150 ms; do not wait for the sentence to end). Record the time and sentence id.
- **Connect** a voice session with the context below, using the onboarding session code.
- **Show that it is waking up.** Within 100 ms of the tap, the circle starts to move: its edge loosens into a slow, organic, breathing shape, as if something is stirring. This is the loader. No text, no spinner, no sound.
- **The guide speaks first.** The moment the session is ready, the circle settles into its speaking state and the guide opens in its own words ("Oh, hey, did you have a question?", "Yes?", varied, written by the guide, never a canned list). The loader gives the listener a natural beat to wait rather than talk over it.
- **Mic timing:** the mic opens when the session is ready, not on the tap. If the listener starts talking during the loader, the guide's opening yields to them as soon as the session hears speech.
- **If connecting takes more than about 3 seconds,** the circle keeps breathing; after 6 seconds it shows one plain line ("Still waking up") and keeps trying; after 12 seconds it gives up quietly and resumes the chapter.
- Do not keep a session open during playback to avoid the wait; connected time is billed.

### The connecting state

Add one state to the onboarding circle's state set, built the same way as the others:

| State | Shows |
|---|---|
| Connecting | The circle's outline deforms slowly and continuously, like a soft membrane or a drop of ink in water: low amplitude, about one breath every 2 seconds, never jittery. Same colour as the other states |

Hand-off to Speaking should morph smoothly, not snap. Draw it in plain canvas or SVG with a few lines of noise, no animation library. Respect the system's reduced-motion setting by falling back to a gentle pulse.

## What the guide is told

Passed in when the session starts, using the same mechanism onboarding uses for the profile:

| Context | Detail |
|---|---|
| Who it is | The guide's identity file and persona, unchanged from onboarding |
| Who it is talking to | The listener profile |
| The chapter | Title, angle, through-line, and the outline (the brief's segment list with one line each) |
| Where they are | Segment and sentence; how far through the chapter, in minutes |
| What was just said | The last 90 seconds of script, word for word |
| What is coming | The next 3 minutes of script word for word, and the outline of everything after |
| What it can draw on | The dossier and the ledger, including parked ideas |
| Earlier asides | Any previous conversations during this chapter, in brief |

If the full dossier makes the session slow to start, pass the entries for the current and next segment in full and one line for each of the rest.

## How the guide behaves in an aside

Add these to the persona as a mode, not a new character.

- **Short spoken answers.** Two to four sentences unless the listener asks for more.
- **Answer from the dossier when it covers the question.** Otherwise well-established general knowledge is fine. No invented quotes, figures or sources. If it doesn't know, it says so.
- **Don't spoil.** If the chapter gets to it soon, say so and give a taste.
- **It is a conversation.** Follow-ups, disagreement and tangents are welcome. Same tone rules as onboarding: never two questions in a row, react before asking.
- **Beyond the dossier:** if a question deserves real research, say it's worth a proper look and call `save_seed`. No live research in this build.

## Getting back to the chapter

This is the hard part. The guide has one tool for it, `resume_chapter`, and these rules for when to call it.

**Go back without asking when:**

- The listener says so in any form ("OK, go on", "let's keep going").
- The question is answered and the listener gives a closing signal ("oh, OK", "got it", "huh, interesting", "thanks").
- The guide has answered, added nothing that invites a reply, and the listener is silent for about 3 seconds.

**Ask first, lightly, only when:**

- There have been several exchanges and it is unclear whether the listener is finished. One short check ("Want to keep going on this, or get back to it?"), at most once per conversation.

**Never** ask "shall I continue?" after a single answered question. After one exchange the default is to return.

**The return itself**

1. The guide says a short lead-in in its own words, different each time. It may point at what is coming: "OK, where were we... oh, right, Diotima."
2. It calls `resume_chapter`.
3. The page ends the voice session, waits about 0.4 seconds, and resumes the recording.

**Where playback restarts**

| The conversation lasted | Restart from |
|---|---|
| Under 30 seconds | The start of the interrupted sentence |
| 30 seconds to 2 minutes | One sentence earlier |
| Over 2 minutes | The start of the paragraph |

The guide can override this by passing `rewind: "paragraph"` when the listener seemed lost.

**Edge cases**

- **Silence after the greeting:** after about 8 seconds, the guide says something like "I'll pick it back up" and resumes.
- **"Never mind":** resume at once, no lead-in beyond a word.
- **Long conversations:** no hard limit. After 5 minutes the guide may offer once to save the thread for a future lesson.
- **Connection drops:** the page resumes the chapter by itself and shows one plain line.

## Tools the guide can call

| Tool | Does |
|---|---|
| `resume_chapter(rewind?)` | Ends the conversation and restarts playback. `rewind` is `sentence`, `previous` or `paragraph`; default follows the table above |
| `save_seed(topic, why)` | Records a topic for a future lesson |

Run them the way onboarding runs its tools.

## On the page

- The circle carries the state during an aside: Connecting, Listening, Thinking, Speaking. No status words except the slow-connection line.
- When the conversation ends, its transcript is added to the episode transcript at the point of interruption as an aside, collapsed to its first line.
- Nothing else new.

## Log

For each aside, append to `<run>/12_asks/asides.jsonl`: time in chapter, sentence id, full transcript, how it ended (listener said so, closing signal, silence, button, dropped), duration, restart point, any seed, time from tap to first sound, and cost. Write seeds to `<run>/12_asks/seeds.md` as well.

## Acceptance checks

Automated:

1. Tap pauses within 200 ms, starts the connecting animation within 100 ms, and records the right sentence.
2. The context builder produces the right 90-second and 3-minute windows at the start, middle, a segment edge and the end of the chapter.
3. Restart points follow the table for each duration band and each `rewind` value, including at segment edges.
4. The button toggle resumes correctly from every state: connecting, guide speaking, listener speaking.
5. A dropped session, or a connection that never completes within 12 seconds, resumes the chapter.
5b. With reduced motion turned on, the connecting state shows the gentle pulse instead.

Scripted conversations. If the voice platform offers a text or simulated-conversation mode, use it; otherwise run the same prompt and tools against the Claude API in text. Write and run 14 scenarios at varied points in the real chapter-one run:

6. One question, closing signal. Expect `resume_chapter` with no check-in question.
7. One question, then silence.
8. Two follow-ups, then "OK, go on".
9. A long tangent of six or more exchanges. Expect at most one check-in.
10. "Never mind."
11. Silence after the greeting.
12. A question the chapter answers within the next 3 minutes. Expect no spoiler.
13. A question outside the dossier. Expect no invented facts.
14. A false premise. Expect a correction.
15. A request for depth beyond the dossier. Expect `save_seed`.
16. The listener disagrees with a claim in the chapter.
17. The listener says they are lost. Expect `rewind: "paragraph"`.
18. A personal remark, not a question.
19. The listener asks the guide about itself.

For each, report whether the return happened at the right moment and in the right way. Have a separate model pass, with fresh context, grade every factual answer against the dossier.

Live, by voice (Claude Code runs what it can; the rest is for Ian):

20. Measure, over 10 runs: tap to animation start, tap to session ready, and tap to the guide's first word. Report median and worst. Record a short screen capture of the connecting animation and its hand-off to speaking for Ian.
21. Leave Ian a five-minute test script: five interruptions to try on the real chapter, covering scenarios 6, 8, 10, 12 and 15.

Spending cap for the build: $10, logged per call.

## Deliverables

- The feature, wired into the merged page and server.
- `ask/REPORT.md`: what was built, results for checks 1-20, the connection-time figures, where the animation capture is, total cost, and Ian's test script.
- A list of every change made to onboarding files, with the reason for each.

Work through the build without stopping for review. Stop only if onboarding or the episode page can't be made to work together after merging; report what conflicts.

## Out of scope

- Research during a conversation. Later, the guide decides when a question needs it and says so before looking things up.
- Starting a conversation by voice alone, without the button.
- Jumping around the chapter by voice ("go back to the part about Plato").
- Changing the rest of the chapter in response to the conversation.
- Images.
