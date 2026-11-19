# What the page and the code tell the interviewer (demo/interviewer.py notes())

One `key: text` per line below the `---`. {name}, {when}, {words}, {talk}, {times}, {title}, {summary} are filled
by demo/interviewer.py before the page or the prompt gets them. `opened_*` go as speakable context the moment a
session starts (GPT-Live has no opening field; live.js greets the same way), and once more as an instruction if
it has said nothing after demo.interviewer.page.greet_fallback_ms; `opened_intro_named` when the tester's name
is known (given with the link, or saved before). `waiting` when they finished speaking
demo.interviewer.page.reply_wait_ms ago and it has said nothing but a backchannel since (Ian's debrief of
2026-11-16: "Mm-hmm", "Hmm.", then 77 seconds of nothing). `handover_now` near
demo.interviewer.intro_max_minutes; `time_up` at debrief_target_minutes, `wrap_now` at debrief_max_minutes.
`meeting_*` and `step_*` are the intro's {{meeting}} and {{first_step}}. Never put product wording in the intro's
lines: the intro must not say what the thing is.
Ian's notes of 2026-11-18 (his second run): the intro is one question and one turn (`step_named` says the
who-check is the one question), and the debrief is told what the tester did. `did_*`, `heard_*` and `times_*`
are the lines of that account ({{did}} in prompts/interviewer_debrief.md and prompts/debrief_summary.md), built
by demo/interviewer.account from the facts demo/debrief.activity reads in the tester's own records; a line is
left out when no record shows it. His debrief had said "Earlier, you chose not to cut in" though he had heard
none of the piece: `asking_*` is the debrief's {{asking}}, and `asking_unheard` is for a tester who heard none.
After the review of that change (2026-11-18): `asking_no_piece` for a tester who ended before any piece was
started (the unheard line spoke of "the piece" and "the wait", and there was neither); `did_ask_taps` and
`asking_tapped` for a tester who tapped to ask and no question of theirs was recorded (they may have spoken and
not been heard: it is told as a tap, never as "never stopped it"). A press of End demo with the opening ready
and unstarted is worded `did_end_talk` (the page reports "making" until the piece is read).
Ian's note on the debrief (2026-11-18): "there was no clear exit... it should have an exit if the interview
seems to be coming to a close... At a natural point that interviewer could say thank you or something about
helping." `farewell` is the debrief's one ending ({{farewell}} in prompts/interviewer_debrief.md;
`farewell_unnamed` when no name is known): the page and the server key on its last sentence
(demo/interviewer.py FAREWELL, PAGE_DEFAULTS["farewell"]; a test checks they match), so change the two together.
`time_up` and `wrap_now` now ask for the farewell, not for the closing question (which is optional).
After the review of that change (2026-11-18): the debrief has its own waiting note, `waiting_debrief` (the
intro keeps `waiting`): "then carry on" was the one page cue left that could push it into another question after
"let's end here", or after a farewell the tester spoke over (the page keys on the farewell's last sentence, so a
cut-off one closes nothing). Keep "waiting for you" in both (tests and transcripts' readers match on it). And
the `asking_*` lines no longer owe a question ("One of your questions is how that went", "Ask what happened"):
the debrief ends when it has enough, so they say what to ask if it does go on; their own moment stays the first
choice.
---
opened_intro: Begin now: say hello warmly and ask their name. Nothing else yet.
opened_intro_named: Begin now: say hello warmly and check who it is: "Hi, is this {name}?" Nothing else yet.
opened_debrief: Begin now: welcome {name} back in a few words and thank them for staying on, then ask your first question.
waiting: [They've finished speaking and are waiting for you. Speak now: answer what they asked or respond to what they said, in a sentence, then carry on.]
waiting_debrief: [They've finished speaking and are waiting for you. Speak now: answer what they asked or respond to what they said, in a sentence. Then your farewell, word for word, if they asked to stop, sound finished, have given you enough, or you had begun it; only otherwise your next question.]
handover_now: [It's time to hand them over: say "I'll hand you over now." and hand them over.]
time_up: [You're at time. No more questions: once they've finished their thought, respond in a few words, then say your farewell, word for word, and end.]
wrap_now: [Time is up. Say your farewell now, word for word, and end.]
farewell: Thank you, {name}. That really helps Ian. That's everything from me.
farewell_unnamed: Thank you. That really helps Ian. That's everything from me.
meeting_first: You haven't met them before and you don't know their name.
meeting_named: You haven't spoken with them yet. Ian told you their name: {name}.
step_first: Say hello and ask their name. Say it back to confirm it ("Sam. Did I get that right?"). If they correct you, use the corrected name and confirm again. Never ask them to spell it. Once it's confirmed, save it (see the Delegation policy), and go straight on to step 2.
step_named: Say hello and check who it is: "Hi, is this {name}?" If they say yes, go straight on to step 2. If they give a different name or correct you, say it back ("Roland. Did I get that right?"), use it from then on and save it (see the Delegation policy). Never ask them to spell it.
asking_moment: {when} in, they stopped it to ask "{words}". Not owed if you already have enough; if you do go on to another subject, make it this one: how that went. Name the moment, when it was and what they asked.
asking_none: They heard the piece and never stopped it to ask anything. You may ask whether they tried interrupting it, or whether anything made them want to.
asking_unheard: They heard none of the piece, so there is nothing to ask here: don't ask about listening to it, how it was read, or interrupting it, and never say they chose not to. If you ask here, ask about what they did do instead: the conversation they had, or the wait.
asking_no_piece: No piece was made for them, so there is nothing to ask here: don't ask about a piece, listening, a wait or interrupting. If you ask here, ask about the conversation they had with the voice.
asking_tapped: They tapped to ask it something {times} while it was read, but no question of theirs was recorded. If you ask here, ask what happened when they tried: don't say they never stopped it, and don't guess why.
did_talk_started: They talked with the voice for {talk} before it started making an audio piece for them.
did_talk_only: They talked with the voice for {talk}. No piece was started.
did_piece: The piece is titled "{title}": {summary}
did_piece_title: The piece is titled "{title}".
did_quiet_asked: They asked for quiet {times} while the piece was being made.
did_quiet_given: The voice went quiet while the piece was being made.
did_tap_talk: While it was quiet, they tapped to talk {times}.
did_status: They asked how the piece was coming along {times}.
did_none_not_ready: They heard none of the piece: it wasn't ready yet when they ended.
did_none_ready: They heard none of the piece: it was ready, but they didn't start it.
did_none: They heard none of the piece.
did_moment: {when} in, they stopped it to ask: "{words}"
did_no_moments: They never stopped it to ask anything.
did_ask_taps: They tapped to ask it something {times}, but no question of theirs was recorded.
did_pauses: They paused it {times}.
did_seeks: They moved to another place in it {times}.
did_end_talk: They pressed End demo while talking with the voice.
did_end_making: They pressed End demo while the piece was being made.
did_end_resting: They pressed End demo while the voice was quiet.
did_end_reading: They pressed End demo while the piece was being read.
did_end_aside: They pressed End demo while asking it a question.
did_end_paused: They pressed End demo while the piece was paused.
did_end_ended: They pressed End demo after the piece had finished.
did_end: They pressed End demo.
did_unknown: The app has no record of what they did: ask, don't assume.
times_1: once
times_2: twice
times_n: {n} times
talk_short: less than a minute
talk_minute: about a minute
talk_minutes: about {n} minutes
when_seconds: A few seconds
when_minute: About a minute
when_minutes: About {n} minutes
heard_all: They heard all of it (about {minutes} minutes).
heard_some: They heard about {heard} of its {minutes} minutes.
heard_little: They heard less than a minute of it.
name_unknown: them
title_unknown: (no title)
summary_unknown: (no summary)
sim_situation_intro: A friend, Ian, asked you to try something new he built and you've just tapped to begin; you don't know what it is. An interviewer's voice greets you first.
sim_situation_debrief: You've just tried it: you talked with a voice that makes an audio piece for you, which you can interrupt with questions. Now the interviewer who welcomed you earlier asks how it went.
