# One live voice: GPT-Live for the guide and the reading (report)

Spec: "One Live Voice (GPT-Live replaces ElevenLabs)", Ian, 2026-11-13. Built 2026-11-13/14 on branch
`claude/hungry-germain-1ac517`. Scope, per Ian's message: onboarding and the live chapter first; the
overnight chapter work (overnight mode, checks 6 and 10) is a later phase.

**In one paragraph.** It works end to end against the real API. Tap the circle: the guide (Gleam)
says hi and asks your name, chats, pitches, and starts a fast chapter through its tools; about three
and a half minutes later it reads the opening to you itself, paragraph by paragraph, as Claude locks
each section. Tap mid-chapter and it stops within a third of a second, answers, and goes back about
two seconds after it leads back in. When the next part isn't written, it checks in, in the same
session. Chapter one read live: 87% of paragraphs word for word, and almost all the rest differ only
in how the transcript spells them. The persona is better than at the start but does not hold yet
(graded 3-6/10): praise and long pitches persist, and going back after a question still leans on the
page's own cues. Costs: $0.05 per connected minute; a 20-minute live chapter is about $1.10 of voice.

## The earpiece (2026-11-20): a plan in the background instead of a director per line

**What Ian said.** Not a turn-by-turn assistant: an earpiece that keeps evaluating the conversation and passes
notes on what to do next or when to wrap up, "perhaps even with a plan that the earpiece agent can use to check
off items or even modify the plan", with no delay to the conversation; the guide keeps "all the other stylistic
instructions on how to sound and interact with the listener".

**Built.** Sonnet 5.5 (effort low) looks every 15 s of talk with something new, at the end of a listener turn,
once after the start and on a 12 s silence; one look in flight, never blocking a turn. It returns the plan
(open/done/dropped items, renamed with the listener's own words), its read, the next step, and a whisper only
when the advice changes (at most 25 words). The page sends a whisper by `session.instructions.append` in the
`direct` wording ("... Work it in your own words when that moment comes, never over them. If it doesn't come,
let it go. This replaces any earlier earpiece note."), `direct_now` / `direct_start` for the few that can't wait.
Replayed offline on run 2, run 3, Pooja, Purnima and three persona runs (110 looks, $0.72): it now catches the
pitch moments it had missed (The God Test at 3:57, the peregrine at 4:47, offering Purnima a surprise at 2:11)
and stays silent through the stories. Review: four lenses, 17 findings confirmed, all fixed with tests (audio
meters as well as the transcript for who is speaking; nothing left over after a rest; no whisper during a tool
call; a quiet whisper no longer rests the page).

**Live probes** (a scripted TTS listener, a whisper injected in the `direct` wording; `persona --probe`):

| Probe | Whisper | What the guide did |
| --- | --- | --- |
| A, mid-story | pitch the peregrine comeback, at 0:31 between fragments | backchannels only in the gaps, no pitch before the probe closed at 1:00 (too short to tell holding from dropping) |
| B, after its own question | offer, at 0:18 | asked another question, then at its 2nd turn (21 s) explained and pitched in one turn |
| C, listener keeps going | offer when the story winds down | reacted twice, then offered at the wind-down (33 s), after they finished |
| D, supersede | octopus pitch, then "drop that: the lighthouses" | octopus never pitched or mentioned again; lighthouse keepers pitched at its 3rd turn (40 s) |

No note was ever spoken aloud ("earpiece" or "producer": 0 hits in every transcript).

**Natural runs** (`persona --director`, evening as Ian and reserved as Priya, 3 minutes each): 22 looks, 0
whispers. Evening: the plan tracked the guide's own offer (1:43), kept "Finish saying what you do" open after
the explanation was cut off and checked it at the full retelling; no pitch by 3:00. Reserved: the guide pitched
the Rumours harmonies at 1:51 and started at 2:15 without ever saying what it does; the director checked that
item off, against its rule (now: a pitch taken up with no explanation leaves it dropped). The guide still asks
a lot of a reserved listener (6 of 8 turns) and takes "Okay, cool." or "Sure." as a yes.

**Not yet known.** A whisper written by the director itself, heard live (none came); a whisper while the guide is
mid-reply; many whispers in one session. Cost: about $0.005 a look, $0.02 a minute. Build checks now $19.11 of
the $20 cap.

## After Ian's third tester run (2026-11-19): warmth back, and a director

**What he said.** The guide was peppier but not engaging; his conversations with Pooja and Purnima had been more
curious and "a little more flirtatious, in a platonic way". He asked for a listening agent over GPT-Live to decide
what to do when. In his run the 50 s offer clock fired while he was mid-story about omega-3s, and the chapter
started on an "Um" while he was asking what it would be about.

**Changed.** The 720f7d5 persona and talk rules are back (warm, curious, a little wry; conversation, not interview;
about a third of turns a question; stay on what lights them up), with the 2026-11-18 pep and pace and the no-body
rule kept. Clear-yes starts; started notes name the topic; the offer to chat after the start waits for an answer.
The director (`voice/gpt_live/director.py`, `prompts/live_director.md`): Sonnet 5.5 at effort low, asked by the
page after each line and on silences, its note delivered only in a lull and dropped if anyone spoke; while it is
up the offer clock is off and the pitch clocks are late fallbacks.

**Replay** (offline, 25 moments, written by the builder): 21 acceptable, median 2.0 s, about $0.003 a call cached;
the omega-3 moment is now `draw_out` (it was an offer); the 4 misses were all `wait` a second after a listener line.

**Live** (`persona --personas ian3,reserved --handoff Ian`, director on, then off; `_runs/20261119-225121` and
`_runs/20261119-230211`):

| | ian3 on | ian3 off | reserved on | reserved off |
| --- | --- | --- | --- | --- |
| Said what it does | 2:25 | 1:17 (offer clock fired mid-line; cut into his pause) | 1:15 | 0:58 (offered and explained in one turn) |
| Pitch / start | none in 2:47 | pitch begun at 2:46 over his story | 1:53 / 2:13 | 1:53 / 2:21 (a second nudge after the pitch) |
| Questions | 3 of 8 turns | 5 of 11 | 5 of 8 | 5 of 7 |

All 12 director decisions were `wait`: the mirror asks only after guide lines, most of which end in a question.
So "on" here measures "clocks off". It removed the cut-ins, at the cost of a slower offer. The director's
own moves are first heard on the real page. One slip: "Friday night salmon" on a Thursday session. Cost $0.70.

## After Ian's second tester run (2026-11-18): fewer questions, more eager

**What he said.** "Too many questions now from the narrator, it should pick up on silence or try to get the
listener to talk a little bit more, overall it should be more eager"; in his debrief: the voice "could have been
a little bit more peppy", and "we were talking a little too much before the ideas were brought into this
conversation ... like when you meet someone new, you're like, So what do you do?". In his run
(`listeners/t-ian-run-2/sessions/20261118-184440.jsonl`) four questions about his day came before the offer at
2:00, and the pitch at 4:50, hedged and about a neighbouring subject. The tester handoff had removed the hello
and name exchange, so "one or two easy exchanges" became four small-talk questions; the 2026-11-16 holds had
pushed the nudge to 3:30.

**Changed** (tests in `tests/live/`; the instruction files' notes above `---` record why):
- Instructions: the persona is eager and never dutiful; conversation is bright and quick (the reading keeps its
  calm voice); a question is the exception and never two turns running (the offer and the pitch excepted); no
  small-talk questions that go nowhere, none about dull work; a lull before a chapter is the guide's cue; the
  offer after one exchange, never in its first two turns; the explanation ends by asking about one particular
  thing (listening to, reading, watching); the pitch as soon as something lights them up, the thing itself; a
  quiet listener is offered a surprise that has nothing to do with their work or day.
- The page: `offer_nudge` (`live.page.offer_nudge`: once, at the first lull from 30 s, by 50 s even while they
  talk; never once the guide has said what it does); `pitch_nudge_secs` [120, 180]; `pitch_hold` 40/20/40.
- The handback notes for End demo (`handback_talk`, `handback_making`; `handback_end` reworded) go as speakable
  context: as instructions GPT-Live let them pass (12 s of silence in conversation, 8 s at the chapter's end).
- `persona.converse(handoff=...)` and `python -m voice.gpt_live persona --handoff NAME` open as the Tester Demo
  does; the `evening` persona (prompts/live_personas.md) is the listener these were checked against; a line
  with "..." is spoken in pieces; `pace()` reports when it said what it does, pitched and started.
- prompts/live_grade.md's criteria follow the new rules (they still graded the 2026-11-15 guide); not yet used.

**Live checks** (five conversations, no grade, about $0.88; `voice/gpt_live/_runs/20261118-192341`, `-193443`):

| Listener | Said what it does | Pitched | Started | Turns that asked |
| --- | --- | --- | --- | --- |
| Ian-like, handoff (before tuning) | 1:16 | 2:19 | 2:48 | 5 of 8 |
| Terse, handoff (before tuning) | 0:23 (she asked) | 2:06 | 2:37 | 5 of 9 |
| Dad, plain hello (before tuning) | 0:17 (he asked) | 1:27 | 1:54 | 4 of 4 |
| Ian-like again (after tuning) | 1:14 | 2:07 | 2:40 | 3 of 13 |
| Terse again (after tuning) | 0:21 | 1:24 | 1:49 | 3 of 7 |

The handback through the real page: nine short runs, $0.21 (speakable: the guide's goodbye begins 0.5 to 0.6 s
after the tap and the debrief 4 to 7 s after it; mid-reply it varies, bounded by the page's 2.5 s rule).

**Not checked live** (a review after the tuning found them; wording only, pinned by tests): on the tuned text
the terse listener got "Want to hear what I do?" as the guide's second turn, 13 s in, then a cold "What's
something you'd be tickled to dive into?", and was pitched the dull work week that was all she had said. Now:
not in its first two turns, the explanation ends by asking about one particular thing, and a pick for a quiet
listener leaves their work and day alone. One run of `--personas reserved --handoff Priya` would show it
(about $0.15). Whether the voice sounds peppier cannot be told from transcripts: its measured pace is unchanged
(3.5 to 3.8 words a second).

**After those fixes, and Ian's further note** ("the narrator ai was relating to human experiences which they
couldn't have any experience with": "Carbonara ... I'm kinda jealous of that", "Plywood can be fussy, but when
it's dialed in, it's so clean"): `00_system.md` now says it speaks as what it is (never eaten, cooked, built,
been anywhere), and the example lines that modelled it are gone ("a lunch like that has me jealous", "a small
piece of yourself"). Two conversations on the final text (`_runs/20261118-220431`, $0.29):

| Listener | Said what it does | Pitched | Started | Turns that asked |
| --- | --- | --- | --- | --- |
| `evening` (Ian-like), handoff | 1:14 offer, 1:38 said | 2:16 (cut by his fragments; again at 2:44) | not within 3 minutes | 4 of 11 |
| `builder`, handoff | 0:44 (he asked) | 1:41 | 2:10 | 3 of 6 |

No claimed experience in either. Closest: "I love the chaos" and "Carbonara! That's bold on a weeknight." Still
a small-talk question in one ("What kind of dinner night was it?"). The offer was not in its first two turns.

## Ian's fifth note (2026-11-16): step back while it researches

"It should default to silence the mic ... suggest she will step back for a bit to research, unless the listener
has anything else they'd like to chat about ... if they were engaging a lot, continuing the conversation with
the research in the background would be a good suggestion ... no point wasting GPT-Live credits."
- **After the start**, the page sends one of two notes. If the listener has been chatting easily (8 lines or
  more, the last four averaging 6 words or more: `engaged_lines`, `engaged_words`, typed lines count), the
  guide says it'll research in the background and is happy to keep talking, or step back if they'd like quiet
  (`started_company`). Otherwise it says it'll step back while it researches unless there's anything else
  they'd like to chat about, and that they can speak up or tap its circle any time (`started_step_back`), and the
  page steps back after that line. "Okay, thanks" or "no, go ahead" doesn't stop it; more than that does. If the
  guide says nothing about it, the page steps back after 12 s. `start_chapter`'s backend reply is empty now.
- **Stepping back closes GPT-Live** (it billed $0.05 a minute muted). The mic is kept on this machine for the
  wake listener (released after an outright mic-off). Each way back is a new session told why: a voice
  (`listening_again`: listen, answer only if spoken to), a tap (`back_from_rest`), play (`continue`), the
  chapter ready (`ready_back`: a session with no mic at all says the ready line, then closes again), typed text.
  A start promised and not kept is reminded before the session closes; a tool call in flight is waited for.
- Live (persona runs, the page's notes sent as the page does): to a chatty listener, "I'll keep the slapstick to a
  minimum while I dig into glowing sea creatures, unless you'd really rather some quiet"; to a reserved one who
  chose quiet, step_back and "Sure, I'll just wait."
- Review: three lenses, two skeptics each; 16 confirmed, all fixed with tests (the main ones: a tap within a
  rest's 1.5 s close acted on the dying session, and the ready announcement was skipped in that window; "okay,
  thanks" cancelled the step back; the ready announcement's reconnect turned the mic on; typed text while
  resting went to the typed guide). Open: a quiet check-in (waiting for the next part) still keeps the session
  open.

**The checks' own cost** (Ian, 2026-11-16: "way too high for these small tests"). Of the $15.84 spent, $6.00 was
GPT-Live time in persona and aside runs of 4-7 minutes each, $3.62 Claude (the simulated listeners and an Opus
grade of every conversation), $3.04 two big page checks (a whole chapter read aloud, a real chapter made end to
end); the recent small checks were $0.08-0.15 each. Now (`config.yaml` `live.checks`): a simulated conversation
runs 3 minutes and 8 listener lines by default and stops one line after the start; grades only with `--grade`,
and then on Sonnet at medium effort; and every check is capped at $0.40 (its session's connected time, and a
page check stops there).

## After Ian's fourth test (2026-11-15)

"Immense improvements, the quality of the conversation is so natural now." Two fixes:
- **What this is, offered once.** A returning listener was asked "Want to hear what I do?" again, though the
  guide's memory recorded telling them. The offer is now in the instructions only for a listener who hasn't
  been told (`instructions/offer_first.md`); a returning one, or a resumed session whose guide has already
  said "audio chapters", gets `offer_known.md` (never offer it again unless asked).
- **Wake on sound too eager.** Each "turn off your mic" came back on within 7-16 s while he talked to his son.
  In his recording his voice to the guide, his son's roar and the kids measured about the same (noise
  suppression leaves the room near silent), so loudness can't tell them apart. Now an outright mic-off
  (`mic_off_ask`) is woken only by a tap; otherwise no wake for the first 15 s of a rest and a longer sound
  needed (600 ms within 1 s, `live.page.wake`). The quiet pattern also missed "microphone" (only "mic").

## After Ian's third test (2026-11-15)

Saved: `listener/_saved_20261115-132151/` (transcripts, memory, the reading log, the 28-minute session
recording) and the run `runs/different-hindu-buddhist-traditions-20261115`.

**What he found.** Much better. Then: the mic went off only when he asked outright ("turn off your mic"), not
when he said he'd be away ("when it's ready we'll get back to ya", "you call me"); wake on a sound, built for
the ElevenLabs page, wasn't on this page; the ready chapter showed only a play button: the page's note reached
the guide while he was talking to his mother, and it said nothing for three minutes; a tap to ask hid the
orange circle (the old page's "membrane", which this page never draws) and the guide answered "Okay, let me
think about that" and nothing more: it had handed the question to the backend, whose instructions said to
answer nothing, and the page's silence note then sent the reading back unanswered; and GPT-Live stayed
connected for ten minutes of a pause.

**Changed** (tests for each in `tests/live/`):
- **Going away turns the mic off**: `quiet_ask` takes leave-taking ("we'll get back to you", "you call me",
  "be right back", "turn off your mic", "I'll pick up the conversation later"), tight enough not to fire on
  "the pain doesn't go away".
- **Wake on sound while resting** (page/wake.js on a copy of the mic track; nothing goes to OpenAI): a voice
  opens the mic with no note and two soft notes play; no words in 12 s and it rests again; two unanswered
  wakes and only a tap wakes it. It never listens while the guide is speaking.
- **The ready chapter is said, not noted**: in conversation the page has the guide say "Your chapter's ready:
  <title>. Want to hear it now?" word for word at the next pause; no play button shows. With the mic off it
  says it's ready whenever they are, and the play button and the circle start it.
- **Asking**: the circle stays; a line that is only a stall ("let me give that a thought", "on it") or a bare
  "okay" is not an answer, and after a silence the page asks the guide to answer (`unanswered`) rather than to
  go back; the backend may answer a question handed to it (from what has been read); every question ends,
  after 30 s of quiet, back in the chapter if answered or paused if not; a restart the listener talks over is
  cancelled.
- **Paused means GPT-Live is closed**: pause cuts the audio and closes the session (and releases the mic);
  play opens a new one told to say nothing and reads on from the sentence; a tap or typed question while
  paused opens one told to listen (`asking`).

**Live check** (the real page in headless Chromium, a TTS listener; `voice/gpt_live/_runs/*-player`). The offer
was said word for word ("Your chapter's ready: 'The Weakest Lever on the Workbench'. Want to hear it now?") and
"Yes, let's hear it" started it; pause cut the voice within 25 ms and nothing was said while paused; play
reconnected and read on from the paused sentence in about 2 s; the slider restarted the reading in 1.4-2.2 s; a
silent tap became a pause; a question asked from a pause ("who was Donella Meadows exactly?") was answered and
"go on" went back; "is that actually true?" got "I can't actually vouch for that" rather than a stall. Two bugs
found and fixed on the way (tests for both): a closed session's last events (its errors, its session.closed)
ended the session play had just opened, and a tap while the pause was still closing asked the dying session.
(The check's own fake microphone handed back a stopped track after a pause; a real browser gives a fresh one.)
Build spend $15.21 of the $20 cap. Ian's run, archived from its recording: `10_audio/chapter.mp3` (6.6 minutes as
heard, four questions placed), a copy in the saved folder.

**Review.** Five lenses, two skeptics each: 22 confirmed, all fixed with tests. The main ones: the first
stall pattern matched real short answers; an aside could stay open forever; a failed reconnect from pause
kept the mic captured; a wake rejected as the guide's own voice left the detector dead; a dropped connection
made the ready chapter's play button do nothing; the leave-taking phrases fired on ordinary talk.

## After Ian's second test (2026-11-15)

**What he heard** (`listener/_cleared_20261115-081810/sessions/20261115-080625.jsonl`). "Hi there, I'm Tavish.
Nice to meet you. And your name?" in one breath; nine questions about his morning before a pitch at 3:40; no
word about what the app does; "I'll give you space" when he asked for quiet, but no `step_back`, so the mic
stayed open; a ring that sat empty for minutes; the opening, ready at 08:14:18, held back 1.6 minutes for a
pause and then read straight over his "Um... well"; no play, pause or slider; and a tap mid-chapter that
seemed to do nothing. That tap did register: the guide said two more words of the passage ("poor part"), the
page took them for its answer, and six seconds of silence later sent the reading back. None of his words
after the tap were transcribed.

**Changed** (each with tests in `tests/live/`):
- **The meeting unfolds.** `opened` asks for a hello alone (live: "Oh, hey!", "Hey, hi.", "Oh, hi there!" in
  five of five persona runs); the name comes on the next turn; then an offer to say what this is, and two or
  three sentences when they say yes (`20_meeting.md`). The page checks in once if the hello goes unanswered
  (`hello_silence`, 7 s) and nudges toward a pitch at 2.5 and 3.5 minutes (`pitch_nudge_secs`).
- **Warmer, less of an interview** (`00_system.md`, `10_talk.md`, `30_pitch.md`): a friendly flirt, a real
  laugh (live: "[chuckle] Feet, huh?", "[laugh] perfect"), guesses and small pieces of itself instead of
  question after question, and company rather than questions while the chapter is made. `start_chapter`'s
  `while_waiting` (the older guides' "keep them talking") no longer reaches the live guide.
- **The ready chapter is offered, never started over the conversation.** At a pause the guide says it's ready
  and asks; the reading starts on `start_reading` (a new tool), the listener's yes (a short one, to the
  guide's latest question that names the chapter), asking for it outright ("let's hear it"), the guide's
  lead-in after an answer that isn't a no, a tap on the circle (it shows a play mark) or the play button.
  "Not yet", "one sec", "in five minutes" leave it; the guide is reminded once after two minutes. Resting
  (they asked to wait for it), it is led into as before.
- **Quiet means the mic goes off.** The page acts on the listener's ask ("some quiet time", "I'll just wait")
  and the guide's reply, with or without `step_back`; in a check-in too. Ninety seconds of silence in
  conversation also turns it off. "Let me think" and the like don't.
- **The ring** fills from the moment the chapter starts (from the run's start and its countdown, or a
  four-minute guess, never going back), is full when the opening is ready, then shows where the reading is.
- **Play, pause and a slider.** The script at 150 words a minute is the timeline: the written part is the
  lighter track, the rest an estimate. Pause stops the guide (the audio is cut at once, a note asks it to
  stop) and turns the mic off; play reads on from the sentence it stopped in; the slider stops it and reads
  from the sentence there. Live: the guide's last word came 0.4 s after pause and it said nothing while
  paused; after a move the reading restarted in 1.6 s. A pause over ten minutes closes the session (it bills
  while open) and releases the mic; play opens a new one told to say nothing. After the last paragraph the
  session stays a minute for the slider.
- **Asking.** A tap pauses the reading and opens the mic. The passage's tail after a tap isn't an answer,
  silence sends the reading back only after the listener said something, and a tap nobody speaks after
  becomes a pause with the mic off (30 s). A tap while paused asks from there. Live: answered "who was
  Donella Meadows?" from a pause and went back on "go on".
- **`scout_topic` left out of the live guide.** In two of three persona runs the voice spawned it at the
  moment of the pitch and stalled ("Hmm", "Mm", "Let me think a sec") for up to three minutes, waiting on a
  result that brings nothing back. Without it: pitch at 3:28 in the dad run, no stall in either rerun.

**Review.** Five lenses over the change, each finding checked by two skeptics: 39 confirmed, all fixed with
tests. The main ones: the first quiet and offer patterns were far too loose ("let me think" would have
muted the listener; "yeah, guitar" to "do you play an instrument?" would have started the chapter); a drop
while paused lost the place; the slider and play did nothing after the end or a stopped chapter; paths that
left the mic open (a failed chapter after a rest, a check-in `step_back`); the end hold calling the end
every 200 ms. A replay after the end also raced ahead (paragraphs already heard were queued at once).

**Persona runs** (Claude listeners, Opus grades; `voice/gpt_live/_runs/20261115-093357`, `-095439`): the
grades stay low (0-5/10) and are harsher than the ear: turns still run three to six sentences, stock
reactions persist, and the guide copied two example lines word for word (replaced). Worth Ian's ear before
more tuning. Build spend $14.71 of the $20 cap.

**Not done.** Turn length: the instructions ask for one or two sentences and GPT-Live often says four; the
next lever is OpenAI's own response-length controls. `scout_topic` could come back in a form that can't
stall, if the research head start matters (in Ian's run its findings weren't used).

## After Ian's first test (2026-11-14)

**What he heard.** "Hey! I'm Juno. What's your name?" ... "Hey, I'm Ian" ... "Here's your chapter, starting
now", then a chapter he hadn't asked for. No get-to-know, no research. (`listener/sessions/20261114-185321.jsonl`.)

**Why.** Not the instructions: they reached OpenAI whole (rebuilt and compared with a saved
`session.started` echo, byte for byte). It was the page. The newest run in `runs/` was my heron test
chapter, finished and archived. The page took any live chapter whose opening was written to be this
conversation's, so it announced it the moment the guide's hello ended and read it as soon as Ian had
answered. The guide made no tool call: `start_chapter` never ran. Every live check had used a fresh
scratch run, so none of them caught it.

**Fixed** (regression tests for each, in `tests/live/`):
- **Whose chapter.** The page reads only this conversation's chapter: the run its `start_chapter` returned,
  or one it takes over. It takes one over only if the run is still being made, or its reading stopped
  less than `live.continue_minutes` (15) ago, as when the page is reloaded or reopened mid-chapter. An
  older finished, failed or stopped chapter is never read, never shown, and `get_status` doesn't report it as
  this conversation's. A taken-over chapter opens with "say you're back" (`reloaded`/`reopened` in
  notes.md) instead of a greeting. The reading goes on from where it was, or from the sentence where Stop
  cut it.
- **The lead-in never cuts in.** It waits for the guide's first line, for the answer to a question the
  guide just asked (up to 12 s), then for a pause from both sides.
- **A promised start that never comes.** If the guide says it's starting the chapter and no `start_chapter`
  follows within 10 s (delegation can miss it), the page reminds it once (`start_nudge`, at most twice a
  conversation). Without this, the gate above would turn a missed call into silence.
- **The greeting.** The page now sends it the moment the session starts, as speakable context, not
  after 2.5 s of silence (live: the startup message alone didn't reliably make GPT-Live speak). It is
  asked again only if nothing has been said after 5 s, never as a second greeting.
- **An opening line** (Ian: "an opener suggestion"). GPT-Live has no opening parameter: every name and
  place tried was refused (`unknown_parameter`), and none is in the docs or the newest SDKs (openai-python
  3.51.0, openai-node 7.57.0). The Playground's "Opening" box is the Playground's own. Its equivalent is
  in: `live.opening` in config.yaml. Set it and those words are what the guide says first: in live probes,
  text sent as speakable context at the start was spoken word for word 3 times in 3, about 1.5 s in. Left
  empty, the guide greets in its own words.
- **Smaller.** A chapter that stops mid-reading is said plainly, not waited for with a check-in. Stop
  keeps a fast run's fields in status.json. A tap after a finished chapter starts a new conversation (the
  circle was dead). The page's notes to the guide are in the conversation's transcript, so a line has its
  cause. The guide picks its name from the conversation's drawn names, as the typed guide does, so the
  name memory records is one the server knows. Each session's record now holds a hash of the instructions
  sent, the tools and the startup message, so "were they applied?" can be answered from the record (OpenAI
  can't return a session's settings afterwards). `pagecheck returning` is a live check of this case.

**Then reviewed** (four lenses, each finding checked by two skeptics): 22 confirmed, all fixed with
tests. The ones that mattered:
- After a tap, the page's position report fell back to "section 1", so a page reopened during a question
  read the chapter again from its opening: the original bug by another route. It now keeps the last real
  place.
- The start reminder's pattern matched the guide's own pitch ("I'll make you a chapter on... want that?")
  and could push a chapter nobody had agreed to. It now ignores any line that asks, and the pattern is
  narrowed to present-tense commitments.
- After "the chapter stopped", Try again wrote the rest, but nothing read it. A chapter that failed before
  its opening was never mentioned. Stop pressed by the listener was called a failure. Reset memory left
  the old chapter to be picked back up by the next tap.
- A chapter written while the listener was away and never begun was unreachable. It is now offered for
  two hours (`live.unread_ready_minutes`).
- The answer wait ran from when the opening was ready, not from the question.

**Checked live after the fixes** (OpenAI, $0.19): `pagecheck returning`, a finished chapter as the
newest run: the guide said "Hey!", asked about the listener's evening and followed up on what they said.
Nothing was read in 44 s. `pagecheck smoke`: 5 of 5 paragraphs word for word, gaps 0.57-0.78 s, the reading
stopped 298 ms after the tap, and went back after the answer.

**The Playground isn't the app.** Its Prompt box, reasoning effort (medium) and web search (on) don't
reach the app. Each session sends its own instructions (about 15,700 characters, from
`voice/gpt_live/instructions/`), backend effort low, and no web search. Research is Claude's fast run,
and web search would let the backend answer for the guide.

**On "it didn't start any research".** Once a chapter is started, the fast run researches it: the scout
searches the web, then the research rows run beside the drafting. The opening is written from the
scout's sources so it can start in about four minutes; the later sections use the full research. Making
the opening wait for the first research row would add a minute or two before the first audio: Ian's call.

## Answers to the doc questions

Checked against OpenAI's current docs (developers.openai.com, the GPT-Live guides and the full event
reference, read 2026-11-13) and then live against the API with this account's key. Where the
docs and the live behaviour differ, the live behaviour is what the code relies on.

| # | Question | Answer |
| --- | --- | --- |
| 1 | Text input, audio output | **Yes, but not as a turn.** GPT-Live is full duplex and decides when to speak; there is no "add a user message, then response.create" for the voice. Text reaches it as appended context: `session.commentary.append` (speakable: "information for the model to say aloud, which it may paraphrase"), `session.instructions.append` (trusted instructions) and `session.thinking.append` (silent), each at most 500 tokens. Live: a paragraph sent as commentary ("Read this passage aloud now, word for word...") is read word for word; a bare `[READ id]` tag is spoken aloud ("[READ s1-p01] A man is..."). Instructions appended while the mic is muted produced no speech at all; with the mic open they are acted on. |
| 2 | A session from a server, audio back to the server | **Yes.** `wss://api.openai.com/v1/live/sessions`, Bearer key, `session.start` first, `session.output_audio.delta` (base64 PCM16 24 kHz) back. The docs ask for input audio (silence included) to keep flowing. Used by the harness (`voice/gpt_live/sim.py`). |
| 3 | "Finished generating" and "finished playing" events | **No, neither exists.** The migration guide: "GPT-Live has no corresponding event marking the end of each spoken response. Track playback in your client." No `response.done`, `output_audio_buffer.*`, `response.cancel` or `conversation.item.truncate`. Live: the output transcript arrives in time with the audio (paced in real time), so the page follows the transcript word by word against the paragraph being read: a paragraph is "finished" when its last words have been heard, and "playing" is the paragraph the transcript is in. |
| 4 | Muting the mic without ending the session | **Yes.** `session.input_audio.mute` / `unmute` (acknowledged `...muted` / `...unmuted`); the session and the voice go on. The page also disables the mic track, so nothing leaves the laptop while muted. Muted time is billed. |
| 5 | Maximum session length; idle billing | **About 2 hours** (not in the docs as a number; `session.started.session.expires_at` was 2 h after start in every live session). Context: 128k tokens including audio; at 90% GPT-Live compacts by itself. **Billing: $0.05 per minute of connected time**, per second, silence and muted time included, plus the backend's tokens. A WebRTC session bills 15 s up front, credited back. |
| 6 | Output transcript and its timing | **Yes:** `session.output_transcript.delta` {delta, start_ms, end_ms} (session-relative, 200 ms steps), and `session.input_transcript.delta` for the listener. Live: deltas arrive in real time with the speech; a delta can split a word ("list" + "ening"); there is no "done" event. The session recording runs on the same clock (checked: the transcript's start_ms for the first paragraph is where it starts in the recording). |
| 7 | Terra's model id, Gleam's voice id, the opening parameter, limits | **Terra** is not a conversation model inside the voice: the voice model is always `gpt-live-1`, which writes the spoken replies itself; Terra is the **backend** that GPT-Live delegates tool work to (`delegation.responses.model`). Two Terras are on the account: `gpt-6.1-terra` ($1/$5 per 1M tokens, pinned) and `gpt-5.6-terra` ($2/$12; OpenAI's own quickstart uses it); which one Ian tried isn't recorded, so `live.backend_model` is one line to switch. **Gleam** is `gleam` (`audio.output.voice`; fixed for a session). **The opening parameter does not exist**: `session.opening` is refused live (`unknown_parameter`), and no greeting field is in the docs or the SDK; it is likely a Playground preset. The documented equivalent is used: the greeting as a developer message in the session's startup input (and, if no word has come after 2.5 s, the same note as speakable context). In every live session the guide spoke first, about 1.5 s after the session started, with a varied greeting. **Limits:** instructions 16,384 tokens (ours: about 3,400) and immutable after start; startup input 128 messages / 8,192 tokens; each append 500 tokens; no stated limit on the number of backend tools (7 used). |
| 8 | Recording and replaying generated audio | **Allowed.** OpenAI's Services Agreement §4.1: the customer "owns all Output"; the only ban on recording voice output found is for the ChatGPT Voice feature, not the API. OpenAI supports it directly: with `store: true`, `GET /v1/live/sessions/{id}/content` returns the session as stereo WAV (listener left, guide right), kept 30 days (checked live). OpenAI's voice guides ask that listeners be told the voice is AI-generated: the page now says so under the transcript. |

**What this changed in the design** (the spec assumed a Realtime-style turn API):

- No feed loop on "response finished": the page follows the transcript, and sends the next
  paragraph when about eight words of the current one are left (sent at once, GPT-Live sometimes
  dropped it; near the end it plays straight on).
- No cancel or truncate on a tap: the page ducks the guide's audio at once and appends an
  instruction to stop; GPT-Live stops within about a second. A queued paragraph can't be withdrawn,
  so the tap's note says it is cancelled, and the reading prompt says never to carry on alone.
- No pruning of read paragraphs (there is no item delete for the voice's history): GPT-Live
  compacts at 90% of its context by itself, and the page opens a new session at a section boundary
  when the context passes 80% or the session nears its 2-hour limit, told the conversation so far.
- The guide's tools run through delegation: the voice says "Generate <|SpawnThinking|>", the Terra
  backend picks the tool, the page runs it on the server (/api/live/tool) and hands back the result.


## What was built

| Piece | Where |
| --- | --- |
| The guide's instructions, one file per part, assembled in name order at session start (Ian's template is the base; persona and tone rules from onboarding, reading mode, the aside rules, the check-in, the pronunciation table, and GPT-Live's three policy headings) | `voice/gpt_live/instructions/00_system.md` ... `80_policies.md`; the backend's in `backend.md` |
| What the page tells the guide (greeting, the reading request, the tap, the check-in, ready notes) | `voice/gpt_live/notes.md` |
| Session config, tools for the Terra backend, opening the session from the page's WebRTC offer (the key stays on the server: GPT-Live has no client keys), cost | `voice/gpt_live/session.py` |
| The chapter as paragraphs (`s<n>-p<kk>`), restart points (the ask-by-voice rewind table by sentence), the question's context, the reading check | `voice/gpt_live/chapter.py` |
| Routes `/api/live/start, resume, tool, line, usage, chapter, context, restart, read, aside, archive`, `/live.js`, `/align.js` | `voice/gpt_live/routes.py` (mounted by `server.py`) |
| The page: one session for conversation, reading, questions and check-ins; the circle and section markers | `page/live.html`, `page/live.js`, `page/align.js` (the transcript matcher) |
| Fast runs read live: no narration step, "ready" means locked, paragraphs instead of audio pieces | `faststart/live.py` + changes in `faststart/` (below) |
| Archive: the session recording cut into the chapter, timed with Whisper, asides placed | `voice/gpt_live/archive.py` |
| Live checks of the real page in headless Chromium with a TTS-spoken listener | `voice/gpt_live/pagecheck.py` (`smoke`, `read`, `asks`, `fast`) |
| WebSocket harness, simulated listeners (Claude), Opus grading | `voice/gpt_live/sim.py`, `persona.py`, `__main__.py`; prompts `prompts/live_*.md` |

**How the reading works.** The page keeps the chapter's paragraphs (from `/api/live/chapter/<run>`,
refreshed as sections lock). It mutes the mic and sends one paragraph as speakable context. It
follows GPT-Live's output transcript word by word against that paragraph (`page/align.js`: a word
may be split across deltas or run together; a word is looked for a few words ahead, or further for a
long one). When the paragraph has been heard to its end it sends the next, which starts 0.5-0.9 s
later: a natural paragraph pause. Each paragraph's transcript goes to `/api/live/read` (the reading
check, `runs/<run>/live/reading.jsonl`, with the session times the archive cuts from). Where the
reading is goes to `fast/player.json` as before, so the fast-start scheduler and check-in logic are
unchanged. A paragraph that never starts is sent again once, then skipped; one that stops short is
taken as read after a quiet.

**A tap mid-chapter.** The page drops the guide's volume at once, opens the mic, appends the stop
note, and records the paragraph and sentence. Once the listener starts asking, it adds the
interrupted sentence and the parts by title as silent context. The guide answers. It goes back by
calling `resume_chapter`; or, when it only says its lead-back ("Anyway, where was I... right."), the
page takes that as the cue; or, after the silence note and a few more quiet seconds, the page goes back
by itself. "Back to the chapter" on the circle does it at once. The reading restarts from the
interrupted sentence (under 30 s), one sentence earlier (to 2 minutes), or the paragraph (longer),
never back across a section start.

**The check-in.** When the next section isn't locked, the page reports waiting; the fast-start run
decides pause or check-in as before. A check-in opens the mic and asks the guide in. When the section
locks, the page waits for a pause in the conversation, asks the guide for a one-line lead-in, and
reads on once it has said it.

## Acceptance checks

| # | Check | Result |
| --- | --- | --- |
| 1 | Feed loop | Pass, automated (`tests/live/test_live_page.py`, stubbed WebRTC): in order, one outstanding (the next goes when the current one is heard to its end: queuing ahead made GPT-Live drop paragraphs or cut sentences, live), a new section only once locked, waiting reported when the queue is empty. |
| 2 | Playing-paragraph tracker | Pass, automated: follows the transcript (split words, sound tags, a lead-in before the paragraph), reports section and paragraph to `fast/player.json`. Playback events don't exist in GPT-Live; the transcript is in time with the audio. |
| 3 | Mic muted for every reading; unmuted only by a tap or a check-in | Pass, automated (every reading sent muted, the track disabled; every note preceded by unmute, since GPT-Live ignores notes while muted). |
| 4 | Tap: cancel, record paragraph and offset, resume from the right sentence | Pass, automated: there is no cancel or audio buffer to clear in GPT-Live, so: volume to 0 at once, the stop note, paragraph and sentence recorded, restart at the sentence. |
| 5 | Context pruning | **Not possible as specified** (GPT-Live has no item delete for the voice). Replaced, automated: a new session takes over at the next section boundary when the context passes 80% or the session nears its 2-hour limit, told the conversation so far. GPT-Live also compacts at 90% by itself. A 38-minute reading ended at 15% of the context; a 22-minute conversation and chapter at 53%. |
| 6 | Overnight re-records | Later phase (Ian). The archive's per-paragraph check against the script already exists (`reading.jsonl`). |
| 7 | Chapter one read live | **102 paragraphs (37.5 min), 89 word for word (87%).** Of the 13 others, 9 differ only in the transcript's spelling (British "honour", "saviours", "defence", "neighbour", "baptise" written the American way; "Vlastos's" as "Vlastos s"), 1 in the tracker's paragraph boundary (a sentence counted to the next paragraph), and 3 are real: one dropped "next", "that is" read "that's", and five words added ("the singular, not the general"). Run: `_runs/20261114-000907-read`. |
| 8 | Gaps and handover | Gaps between paragraphs (101): **median 0.78 s**, worst 6.6 s (three over 2 s: a paragraph that started late). Ten interruptions (`_runs/20261114-004812-asks`): the reading stopped within **0-370 ms** of the tap (median about 180 ms; 0 when it was already between sentences), and the first word of the reading came **1.4-2.3 s** (median 2.0 s) after the guide went back. `resume_chapter` was called in 10 of 10 there. |
| 9 | Five ask-by-voice scenarios in the single session | Run 4 rounds by the persona harness (closing signal, follow-ups, "never mind", spoiler question, lost). The guide now waits and answers after a tap (it used to read on from the context). Going back by the tool alone: 2/5 at first, 4/5 after tuning, 1/3 on the last run; the page's lead-back cue and its silence fallback now go back in every case. The spoiler question is still answered from general knowledge ("she puts the mindset on top"): the guide no longer knows what is coming word for word, which was what made it read on. |
| 10 | Overnight recording | Later phase. |
| 11 | Cost of a 20-minute live chapter with two interruptions | **About $1.10 of GPT-Live** (connected minutes at $0.05, questions included; backend tokens under $0.02), plus the onboarding minutes. Measured: the 22.4-minute check-12 session (5 minutes of conversation, 18 of chapter, one question) cost $1.13; the 38-minute read $1.91. The chapter's writing is the fast run's Claude cost ($3.28 for check 12). |
| 12 | A fast-start run in live mode | `_runs/20261114-001038-fast`, run `herons-hunt-standing-still-20261114`: the guide scouted the topic during the chat (`scout_topic` at 48 s), started it (`start_chapter` at 84 s) and noted the listener's question (`add_note`). **First audio 206 s after the topic was set** (scout 44 s, outline 99 s, opening 63 s; no narration step now). Sections 2-4 locked at 7.7, 10.2 and 11.5 minutes, each before the reading needed it: **no holds, no check-ins**. One question mid-chapter, answered honestly ("I don't have an exact number"). 43 paragraphs, 37 word for word (the rest: "Katzir" transcribed "Katsir", one "do not" read "don't"). |
| 13 | Existing pieces still work | **The whole offline suite passes: 1,033 tests** (after the review's fixes and their regression tests). Earlier, under the load of four live checks at once, one subprocess timing test (`test_stop_interrupts_a_run_first`) failed once; it passes alone and in the final run. The ElevenLabs page and agents are unchanged and come back with `live.provider: elevenlabs`; `?chapter=<run>` still opens the old page (finished chapters; questions there still go to the ElevenLabs aside agent). The episode page plays live chapters once archived (the herons chapter, below). A last live smoke run on the final code: 5 of 5 paragraphs word for word, gaps 0.5-0.9 s, the tap stopped the reading in 188 ms, question, check-in and hand-back all worked. |
| 14 | Persona | Three graded rounds (below). Better, not holding yet: onboarding 5-6/10 at best, asides 2-6/10. |
| 15 | Ian's script | Below. |

Spending: the build has spent **$12.05 of the $20 cap** on OpenAI (GPT-Live sessions, TTS for the
simulated listener, transcription), logged per call in `voice/gpt_live/build_costs.jsonl`
(`python -m voice.gpt_live spend`); about $2.50 of Claude went to grading and simulated listeners
(included in that log), and the check-12 fast run cost $3.28 of Claude (its own run log).

## The archive

When a chapter has been heard to the end, the page asks for `/api/live/archive`: the server waits
for OpenAI's recording of each session (`store: true`, kept 30 days), cuts each paragraph from the
guide's channel at the transcript's times (trimmed to the speech; a restarted paragraph is joined
at the sentence where it restarted, found with Whisper word times), writes the pieces in the layout
the episode page expects, stitches `10_audio/chapter.mp3`, times it with Whisper (`timing.json`,
`episode.json`), places the questions and check-ins as text asides, and points `status.json` at the
audio. Checked live: a smoke chapter (every piece starts and ends on its paragraph's first and last
words, by transcription) and the 38-minute read (102 of 102 paragraphs, 37.5 minutes, about 6 minutes
of Whisper). The real check-12 chapter (`runs/herons-hunt-standing-still-20261114`): 43 of 43
paragraphs, 17.3 minutes, timing rebuilt, its one question placed as an aside at 0:45 (sentence
`seg01-s010`), `status.json` pointing at `10_audio/chapter.mp3`. Not yet checked live: a restart joined
mid-paragraph (the herons question restarted at a paragraph's start; the join's Whisper trim was checked
on real audio on its own).

## Persona grading (check 14)

Simulated listeners (Claude, three personas: a talkative builder, a reserved listener, a curious
teacher) talked to the real guide by voice; Opus graded each transcript in a fresh context against
the instruction parts, quoting every miss; the instructions were edited and the round run again.
Runs: `_runs/20261113-234015` (round 0), `20261114-000914`, `001541`, `002321` (rounds 1-3),
`003553` and `004312` (asides after the context change). Total for this check about $7.

| Round | builder | reserved | teacher | closing | follow-up | never mind | spoiler | lost | resume_chapter called |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 6 | - | - | 3 | - | - | - | - | 0 of 1 |
| 1 | 5 | 5 | 4 | 2 | 2 | 4 | 2 | 4 | 2 of 5 |
| 2 | 5 | 5 | 0 | 3 | 3 | 4 | 3 | 0 | 4 of 5 |
| 3 | 6 | 6 | 3 | 3 | 3 | 2 | 2 | 3 | 4 of 5 |
| after the context change | | | | 6 | 2 | 4 | 2 | | 2 of 4 |

**Instruction changes it led to** (each answers a quoted miss; the files' notes above `---` record why):

- Talk (10): one question a turn, "one question mark", no tag question before it or list of options
  after it (missed: "That's the heart-in-throat moment, isn't it? You making the boxes frameless or
  thinking face frames later?"); no verdicts on what they said ("Whoa, that's a serious project!",
  "Good to know!"); usually under twenty words, one move a turn; never reason aloud ("I think I see
  where you're headed") or stall aloud ("Hang on", "Checking"); not even a passing remark about their
  own materials or trade ("birch has that tidy edge...").
- System (00): no invented past ("It's from a novel I love", then "I haven't read it").
- Meeting (20): no "why" or "what it means to you" questions; don't dig into work that bores them.
- Pitch (30): one or two short sentences, starting with the idea, aimed at something that lit them
  up, never a lesson in their own work (the first trial pitched a carpenter a chapter on taming his
  table saw; later pitches went to his grandfather's hand planes); pitch even when they ask outright;
  start the chapter before giving any time; waiting replies under fifteen words; notes go to add_note
  without announcing it.
- Asides (50): open with a word or two after a silent tap, then wait; an answer turn ends with the
  answer; don't give away what's coming even when asked straight out; lead back and hand over in the
  same breath.
- Policies (80) and backend: `resume_chapter`'s trigger made concrete (a closing signal, "go on",
  "never mind", or the silence note) and "only the backend can restart it" (2 of 5 to 4 of 5);
  backchannels almost none and never a turn of their own; the backend replies with nothing when no
  tool fits (it had voiced a spoiler).
- In code, from what grading showed: the context at a tap no longer carries what's coming word for
  word or the outline's notes (with them, the guide read on or gave away later parts in 15 of 15
  graded asides; without, it waits and answers); it goes in once the listener starts asking; the
  live `resume_chapter` description no longer contradicts the lead-back rule; the silence note asks
  the guide to go back.

**Still missing, quoted from the last rounds:** verdicts and stock reactions ("The fingernail test, I
like that.", "That's great. Those are the moments that stick.", "Perfect."); pitches with a wind-up
("You know, there's this moment where that bass line almost tells a little story all by itself,
and I'm thinking, okay, I've got an idea..."); two-sentence waiting turns; the teacher asked for
agreement before pitching and said "Perfect. I'll get to work." before calling `start_chapter`;
"Anyway, where was I... right." copied word for word from Ian's example (kept: it is Ian's line, and
it is now the page's cue to go back); after a tap, sometimes a sentence about the chapter before the
listener asks; and the spoiler question answered from general knowledge. GPT-Live writes its own
replies; it follows the shape of the rules but not their letter, and it reacts to added context by
talking about it. The grader's own errors (about a third of the misses it raised early on: harness
artifacts such as two paragraphs queued at once, or transcript cuts at tool time) are listed in the
round notes and were discounted.

## Code review

An independent review (four lenses: the page's state, the server, the fast-run integration, the
archive; every finding checked by a separate skeptic) confirmed 14 bugs and refuted one. All fixed,
each with a regression test:

- The page: "Back to the chapter" during an aside could start the reading twice (a restart request
  still in flight); a lead-back, the tool and the silence fallback could each start a restart; a step
  waiting on the guide (a lead-in, a resume) survived a dropped connection or Stop and "read" into
  nothing; the chapter could begin while the connection was down; after reconnecting, the mic track
  stayed disabled; the restart position could be stale after a second drop or Stop; "Back to the
  chapter" in a check-in could skip a paragraph; a typed question mid-chapter never got its context;
  tapping the next part while it's still being written did nothing (Ian's script used it to bring on
  a check-in; it now skips to that part and waits for it); typing after a drop started a new
  conversation; a session's cost wasn't logged on Reset or when the page closed first.
- The server: a fast chapter started from the typed chat (or the text box) was marked to be read
  live, so nobody could ever hear it: only the page's GPT-Live conversation marks its chapters live
  now; a seed lookup could continue a run in the other voice; a resumed session forgot the name the
  guide had given itself once the earlier transcript was cut.

## Changes to existing files

- `server.py`: serves `page/live.html` at `/` when `live.provider` is `gpt_live` (the old page for
  `?chapter=` and with `elevenlabs`); mounts `voice/gpt_live/routes.py`.
- `config.yaml`: new `live:` section (models, voice, prices, page timings, the lead-back pattern, caps).
- `faststart/base.py`: `live_voice(cfg)`, `is_live(r)`, `voice_matches`; `mark_fast` marks a new run
  `"voice": "live"` when the page's GPT-Live conversation started it. `faststart/routes.py`: `mode_args`
  takes `live`. `server.py`: `start_seed(..., live)`, `new_session(live)`.
- `faststart/live.py` (new): the live "narrator": a locked section's paragraphs in
  `10_audio/secNN/live.json`, handed to the scheduler as pieces with no audio.
- `faststart/run.py`: live runs use it, skip the ElevenLabs balance check, never say "Recording", have
  no "Recording the opening" step, finish with `audio: null` until the archive has cut the audio.
- `faststart/archive.py`: a live run's archive writes the script and brief without audio; an archive
  cut from the recordings is never overwritten by estimates.
- `faststart/status.py`: `voice: "live"`, paragraphs as pieces, the opening steps without the
  narration step. `faststart/timings.py`: live and ElevenLabs medians kept apart; narration 0 for live.
- `faststart/checkin.py`, `faststart/narrate.py` (a Piece's audio may be None), `stages/seed.py`
  (a finished live run counts as finished).
- `tests/conftest.py`: the `cfg` fixture pins `live.provider: elevenlabs` for the older tests;
  `tests/faststart/fast_run_fakes.py` and two fast tests the same.
- `CLAUDE.md`, `README.md`, `.env.example` (`OPENAI_API_KEY`), `.gitignore` (`voice/gpt_live/_cache/`, `_runs/`).
- Untouched and unused while `provider` is `gpt_live`: `page/index.html`, `page/ask.js`,
  `page/fast.js`, `stages/voice.py`, `stages/tts.py`, the ElevenLabs keys. Not removed: the spec asks
  for that in a separate commit once checks 1-14 pass, and check 14 doesn't hold yet. The pre-made
  ElevenLabs covering clips the spec mentions don't exist in this repo.

## Where this departs from the spec, and why

- No `[READ <id>]` tag: it is spoken aloud. Paragraphs go as a plain request, the id stays in the page.
- No "send the next paragraph on generation finished": no such event. The next goes when the
  transcript shows the current one finished; queuing one ahead made GPT-Live drop or cut paragraphs.
- No opening parameter: the API refuses it. A developer message in the startup input does the same.
- No pruning of read paragraphs: impossible in GPT-Live; a new session at a section boundary instead.
- The context at a tap is the interrupted sentence and the parts, not 90 s + 3 minutes word for word
  + outline: with those, the guide read on and gave away what was coming in every graded aside.
- Terra is the tool backend, not the conversation model: GPT-Live writes every spoken word itself.
- Two ways back from a question besides `resume_chapter`: the guide's lead-back line, and the page
  after a silence (GPT-Live skips the tool call in about half the asides).
- Overnight mode, checks 6 and 10: the next phase, as Ian asked.
- The guide's instructions follow Ian's template where it differs from the tuned ElevenLabs prompt:
  it says its name and asks theirs in the hello (the old prompt held the name back), and pitches after
  five to eight exchanges (the old one after three or four).

## Open items

1. **Persona** (check 14 doesn't hold yet): GPT-Live keeps its own habits (stock reactions, wound-up
   pitches). Worth Ian listening to one conversation before more tuning: some of what the grader marks
   may sound fine spoken. The next lever is OpenAI's own optional controls (response length) and
   custom voices' personality prompts, or moving the pitch decision to the backend.
2. **Facts in answers**: the spec says facts come from the dossier passed into the session. The
   guide now answers from what it has read aloud and its general knowledge; the dossier isn't passed
   (the context limit is 500 tokens per append, and more context made it talk). A question could go
   to the Terra backend with the dossier in its instructions (`session.update` can change them).
3. **Finished chapters** (`?chapter=`) still use the ElevenLabs aside agent for questions.
4. **Resting** (`step_back`) keeps the session open and muted (billed at $0.05 a minute) and closes
   it after 20 minutes; there is no local wake-on-voice as the old page had.
5. A chapter's last section read: the session stays `end_hold_secs` (60) for the slider or a word, then
   closes. No closing line from the guide (the script's own ending is the end).

## Ian's script (check 15)

About 30 minutes, about $1.50 of GPT-Live and $3-5 of Claude for the chapter. Headphones help (the
mic stays open while you talk to the guide; the browser's echo cancellation is on).

1. `cd` to this worktree and start the server: `.venv/bin/python server.py` (or the main checkout once
   merged), open http://127.0.0.1:8765. For a first meeting, press Reset memory first (it moves what the
   guide knows to `listener/_cleared_<time>/`). An older chapter in `runs/` is no longer read by itself. The page looks as before, with "The guide's voice is
   AI-generated." under the transcript toggle.
2. **Tap the circle.** Allow the mic. Within about two seconds the guide says hello, just that. Say hi
   back: it gives its name and asks yours. A couple of easy exchanges, then it offers to tell you what it
   does (two or three sentences), then a few more about you.
3. **Let it pitch.** After three or four exchanges past that (the page nudges it at 2.5 minutes if it
   hasn't) it offers a chapter in a sentence or two, tied to something you said. Say yes (or steer it).
   It starts the chapter itself; the ring round the circle starts filling at once.
4. **While it's made** (about four minutes): talk if you like. Or ask for quiet, or say you'll be back
   ("we'll get back to you when it's ready"): it says a few words and the mic turns off. Speak up or tap to
   come back (a voice wakes it; the page plays two soft notes).
5. **The opening is ready**: if you're talking, at the next pause the guide says it's ready and asks whether
   you'd like to hear it now; say yes (or "not yet": it reminds you once, or say "let's hear it" later). With
   the mic off it says it's ready whenever you are, and the play button and the circle start it. The slider
   appears once the reading starts.
6. **Pause, play, the slider**: pause cuts the voice at once and closes the GPT-Live session (nothing is
   connected or billed while paused); play reconnects (a second or two) and reads on from the sentence it
   stopped in. Drag the slider back (or forward, within what's written: the
   lighter part of the track): it stops and reads from there. Times are estimates from the script.
7. **Ask one question**: tap the circle mid-paragraph. The reading stops; say your question. When
   you've had the answer, say "OK, go on" (or just stay quiet): it leads back in a few words and the
   reading picks up at the sentence you stopped it in. If you tap and say nothing, after 30 seconds it
   stays paused (session closed). Tapping while paused asks from there.
8. **A check-in**: if the reading catches up with the writing, the guide comes in by itself and asks
   what you make of it so far. Answer once. When the next part is written, it leads in and reads on.
   (To force one, tap the next part's marker while it still says "being written".)
9. At the end the session stays a minute (the slider can take you back), then closes. A few minutes later the chapter is archived from the
   recording: `runs/<run>/10_audio/chapter.mp3`, and the episode page at
   http://127.0.0.1:8765/episode/?run=<run> (the transcript under "Show transcript" has everything that
   was said).

What to listen for: whether the reading sounds like the narration you want (pace, pauses between
paragraphs), whether the hello and the chat feel like the template, how quickly it stops on a tap, and
anything the guide says during the chapter that it shouldn't. If voice doesn't start, the text box
comes out and the typed guide (Claude) takes over. `live.provider: elevenlabs` in config.yaml puts the
old voice back.
