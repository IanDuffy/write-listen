# Tester Demo: checks that need a person

The spec (`docs/tester-demo.md`) says checks 5 (by voice), 11 and 12 need a person. Set up the droplet first
(`DEPLOY.md`), or run locally for 5:

```bash
WL_DEMO=1 .venv/bin/python server.py
.venv/bin/python cli.py study create "Ian test"   # prints the link; open it in Chrome
```

(Locally the admin page is http://127.0.0.1:8765/admin.)

## Rehearsal chapters (2026-11-20): testing the voices without making a chapter
- Make your link with `wl study create "Ian run 4" --name Ian --rehearsal` (or set "Chapter: rehearsal" on the admin
  page). Asking for a chapter then replays the heron chapter at no cost: the opening is ready in about 1.5 minutes,
  part 2 about four minutes later. It will be about herons whatever you asked for. Friends' links stay real.
- While you wait, ask "how's it coming along?" or "what are you working on?": the guide should tease one concrete
  thing (something it's looking into or found), not "writing the intro".

## After Ian's third tester run (2026-11-19): what to look and listen for on the next one
- **The intro**: "Hi, is this Ian?", then about 15 seconds on what to expect, "Ready?", and the handover.
- **The guide**: warmer and more curious, closer to Pooja's and Purnima's conversations: guesses about you,
  reactions, about a third of turns a question. It should never cut into a story to say what it does.
- **The earpiece** (on; it replaced the director): a second model listens in the background, keeps a plan
  (meet you, say what it does, find what lights you up, pitch, a clear yes) and whispers the guide a nudge when
  the guide is stuck or past a moment. It should mostly stay silent; when it does nudge, the guide should take it
  up a turn or two later, never mid-sentence, never saying the note aloud. If the guide ever talks over you or
  feels slow to get to the point, note when. `live.director.enabled: false` in config.yaml puts back the timers.
- **The start**: the chapter begins only after a clear yes; "What would it be about?" gets an answer first. Then
  it offers to keep chatting and waits for your answer: "no, go ahead" rests it, anything else keeps talking.
- **End demo while it's being made**: the chapter stops (the admin page shows it stopped) and the debrief says so.
- **The debrief**: no "Just a second" or "I'm still here"; it asks, responds, and ends on "Thank you, Ian. That
  really helps. That's everything from me." (testers hear "That really helps Ian.").

## After Ian's second tester run (2026-11-18): what to look and listen for on the next one
- **The intro.** "Hi, is this Ian?", then one turn on what to expect (it names the End demo button), then "Any
  questions, or are you ready to try it?" and the handover: 30 to 40 seconds.
- **The handover.** Nothing on screen invites a tap while the dot grows into the circle (it was the gate's own
  "Tap to begin" coming back).
- **End demo**, upper right, from the moment the guide's screen appears: through the conversation, while the
  chapter is made and while it is read. Tap it at three different moments on three runs (talking, resting,
  mid-chapter): each should go to the debrief with nothing to confirm, the guide saying a sentence of goodbye
  first when it is there. On a phone it should sit clear of the notch. Not tried with a real microphone or on
  a phone yet.
- **The guide.** Fewer questions, brighter, and it should use a pause to say "Want to hear what I do?" within a
  couple of turns (not as its second line), then ask about one particular thing (what you've been listening
  to or reading), and pitch that as soon as you light up. Whether the voice itself sounds peppier only you can
  judge: the words are livelier, its pace measured the same.
- **The debrief.** About three minutes: "What's one thing that stood out to you?", at most two more questions
  that fit what you actually did (it is told: how long you talked, quiet asked for, taps, questions asked
  mid-chapter, where you pressed End demo). It should never ask about something you didn't do.
- **The debrief's exit** (your note: "there was no clear exit... the interviewer kept asking questions"). It
  should stop by itself once you've given it two or three real comments or sound finished, and end with "Thank
  you, Ian. That really helps Ian. That's everything from me.", after which the screen moves on within a few
  seconds. Say "let's end here" mid-way on one run: the farewell should come at once, with no "anything I
  didn't ask?" first (that question is now optional, for someone who clearly has more to say). Not tried with
  a real microphone; whether "enough" comes too early or too late for a real talker is your ear. Two things
  changed after a review and were not run live: it should not say its farewell on a first half-sentence ("it
  was good, I, um...") but wait or follow up once; and if you say nothing at all in the debrief it still ends,
  but is not marked done (End demo comes back, admin does not show "Done").
- **The final debrief wording, run live** (after that review): ten simulated debriefs, two personas (one who
  trails off mid-sentence, one like your run who heard none of the piece), all ended on the farewell after two
  or three questions, 50 to 105 seconds, none on a first half-sentence. $0.77 (the sim's default is ten runs; I
  meant two).
- **The narrator speaking as what it is** (your note: "relating to human experiences which they couldn't have
  any experience with"). It should no longer envy your dinner or give a verdict on plywood as if it had handled
  it; it can still be curious, guess about you and say what strikes it. Two simulated conversations showed
  none; the nearest were "I love the chaos" and "Carbonara! That's bold on a weeknight." Say if those are still
  too much.

## After Ian's first tester run (2026-11-16): what to listen for on the next one
Make the link with a name (`cli.py study create "Ian test" --name Ian`, or the admin form, which shows what the
interviewer will say). Then:
- **The intro's length and warmth.** It should open "Hi, is this Ian?", say Ian built it and how the session
  will go, ask for questions, and take 60 to 90 seconds (30 to 40 since 2026-11-18): welcoming, not long. It must not hand over before its
  final "I'll hand you over now."
- **The handover.** The guide's first words should come about 2 to 3 seconds after the interviewer's last, by
  name, with no glitch in the sound and no jump on screen.
- **Tap to talk after a rest.** Let the guide step back for a couple of minutes, tap the circle: it should say
  it's here and stay. With the chapter ready, the play button stays on the bottom bar while you talk.
- **The pitch.** It should explore before pitching, not pitch the first thing you mention; say no once and it
  should ask rather than guess again.
- **The debrief replying.** Answer in fragments and ask it something back: it should respond in words within a
  couple of seconds every time, never "Mm-hmm" and silence.
- **On a phone:** the bottom bar clear of the home indicator, and End demo in the upper right clear of the notch.

## 5. The guide uses the name from the interviewer and never asks for it
Open a tester link, tap, give your name to the interviewer, and listen to the guide's first words after the
handover: it should greet you by name and give its own, and never ask your name.

## 6 (length). A real 10-minute chapter lands at 9-11 minutes
Pick a topic with the guide and listen to the end (or check the admin page's minutes afterwards). About $3-4 of
Claude and $0.50 of GPT-Live.

## 11. On the droplet over HTTPS: mic and audio on iPhone Safari, Android Chrome and a laptop
For each: open a tester link, tap, hear the interviewer, talk, hear the guide after the handover, ask a question
mid-chapter, finish (or tap End demo), do the debrief. Note anything that stays silent or asks for the mic twice.

## 12. Two testers at once
Two people (or two phones on two different tester links) at the same time: each hears only their own guide and
chapter, the admin page shows both, and neither's library shows the other's chapter.

# Overnight Chapters: checks that need a person

The spec (`docs/overnight-chapters.md`) says checks 10, 11 (by voice), 12 and 13 need a person. They
are listed here rather than marked passed. What each one already has in code is in `PROGRESS.md`.

Start the server from this checkout and open the library page:

```bash
cd /Users/ian/Desktop/write-listen/.claude/worktrees/spec-overnight-chapters-43fe6b
.venv/bin/python server.py
```

Then open http://127.0.0.1:8765/library (Chrome; allow the microphone).

## 10. A real scheduled 2:00 build on the laptop finishes by morning

1. Keep the laptop on power, lid open (or with an external display), and set it to wake before the build:
   System Settings → Battery → Options (or `sudo pmset repeat wakeorpoweron MTWRF 01:55:00`). This is a
   system setting, so it is yours to change; nothing here changes it.
2. Start the scheduler. **On this Mac a launchd job can't run it**: macOS refuses background jobs access to
   `~/Desktop`, where the repo lives (tried 2026-11-15: "Operation not permitted"). Instead, in a Terminal tab,
   from the main checkout, run this and leave it running. It ticks now and every quarter hour, plans at 18:00
   and builds at 02:00 on build days (following `settings.json`), and keeps the Mac from idle-sleeping:
   ```bash
   ./wl overnight wait
   ```
   (The launchd entry, `./wl overnight schedule install`, works once the repo is outside `~/Desktop`, or once the
   venv's python and `/bin/sh` have Full Disk Access in System Settings → Privacy & Security; your call.)
3. Before 18:00 on a weeknight, check the card plans: `./wl overnight status`.
4. Next morning: the library page's latest chapter is that night's, and
   `listeners/ian/chapters/<date>-<slug>/status.json` says `ready`. The build log is
   `shared/logs/overnight.log`. While it runs, the build holds off idle sleep itself (`caffeinate -i`).

Pass: the chapter is ready by morning, without anyone touching the laptop after 18:00.

## 11. Change by voice, before and after 2:00

1. Before 2:00: tap **Change** on the Up next card. The guide speaks first and names tonight's plan by
   title. Ask for something else, let it say what it will make, and stop talking.
   Pass: the card shows the new plan within about a minute (a planning pass measured 40-45 s live).
2. After 2:00, while the build runs (or with the build start set a few minutes ahead in Settings): tap
   **Change**, ask for a new topic.
   Pass: the guide says tonight's chapter can't change and that it will be the next one;
   `listeners/ian/queue.jsonl` has the request; the next planning pass uses it.

## 12. Play: the intro, then the chapter (10 runs)

Press **Play** on the latest chapter ten times, letting each intro run (talk back on some, stay quiet on
others).
Pass, each time:
- the guide speaks first, names the chapter and mentions one or two things from the intro brief;
- it says nothing from the chapter beyond `intro_brief.md`;
- the recording then starts from the beginning, either when the guide calls `start_chapter`, or after its
  lead-in line and about 1.5 s of silence, or at 90 s at the latest.

How each run ended is recorded in that session's line in `listeners/ian/sessions/<id>.jsonl` (`ended`:
`tool`, `lead-in`, `timeout` or `button`). `python -m overnight.tally` counts them.

## 13. A tap during a recorded chapter

While the recording plays, tap the button (it reads "Ask a question").
Pass:
- the recording pauses and the guide answers;
- after "OK, got it" (or silence) the guide leads back in its own words;
- the recording resumes from the interrupted sentence (under 30 s), one sentence earlier (up to 2
  minutes), or the paragraph's start (longer).

The aside is logged in the chapter's `12_asks/asides.jsonl` with its restart point.

## Costs to expect

GPT-Live bills $0.05 per connected minute: about 5 cents per intro and a few cents per Change or question.
A nightly build was about $8-10 in the live check (`overnight/REPORT.md`).
