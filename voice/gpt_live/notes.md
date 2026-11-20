# What the page tells the guide in the live session (page/live.js), one `key: text` per line

Sent by the page into the one GPT-Live session. `opened` goes as speakable context the moment a new
conversation's session starts (live, 2026-11-14: a developer message in the startup input alone did not
reliably make GPT-Live speak first; context appended at the start did, in about 1.5 s). With
config.yaml `live.opening` set, `opening_words` goes instead, with those words. `reloaded` is the first
(developer) message of a session opened on a page reloaded mid-chapter, `reopened` while its chapter is
still being made and its reading hasn't begun. `read` prefixes every paragraph of the chapter,
sent as speakable context (session.commentary.append): live tests found that a bare "[READ id]" tag
is spoken aloud, and that instructions appended while the mic is muted are not acted on, so the
bracketed notes go as instructions with the mic open and the reading as commentary with it muted.
`context` heads the silent context sent when the listener taps mid-chapter (session.thinking.append).
`continue` and `returned` are the first (developer) message of a session that takes over a
conversation already going, instead of `opened`: at a section boundary, or after a dropped connection.
After Ian's second test (2026-11-15): `opened` asks for a hello alone (the name and the first question come
after); `hello_silence` once if the listener says nothing after it; `pitch_nudge` and `pitch_nudge_again` at
live.page.pitch_nudge_secs into a conversation with no chapter; `ready_again` once if a ready chapter is
still waiting; `moved` for the slider.
After Ian's third test (2026-11-15): the ready chapter is said, not noted (the note had gone unsaid for three
minutes): `offer_line` in conversation and `ready_line_resting` with the mic off, each sent through
`opening_words` as speakable context. In a question mid-chapter, `unanswered` after a silence when the guide
only stalled, `silence` when it answered. `asking` is the first message of a session opened by a tap while
paused (pause closes the session); `continue` is play's.
After Ian's fifth note (2026-11-15): when the chapter starts the guide steps back by default (`started_step_back`) or, if
the listener has been chatting easily, offers to keep them company (`started_company`). Stepping back closes the session,
so a rest ends in a new one: `back_from_rest` (a tap), `listening_again` (a voice), `ready_back` (the chapter ready).
Tester Demo (2026-11-16): `handoff` is what the page sends, as speakable context, the moment the interviewer
has handed a tester over (the guide's session is already open by then), instead of `opened` ({name}: what they
told it); the page asks once more with it if the guide is silent. `handback_end` when the chapter ends (instead of the end
hold) and `handback_done` when they stop it part-way (I'm done then; End demo now, below): the guide closes, then
the interviewer's debrief follows.
After Ian's tester run (2026-11-16): the pitch nudges wait for real interest (the first had come 12 s after the
guide's first open question, and it pitched the first thing he said, which he'd called "not that compelling");
the page also holds them after the guide's explanation and its latest question (live.page.pitch_hold).
After Ian's second tester run (2026-11-18: "it should pick up on silence", "jump quicker into ... what they do",
"it did a good job at trying to find the silence and then just said, so you want to hear what I do?"; the offer
had come two minutes in and the pitch at 4:50): `offer_nudge`, once, at the first lull from
live.page.offer_nudge.after_secs into a conversation whose guide hasn't yet said what it does (by max_secs, even
while they talk); the pitch nudges come sooner and ask for the thing itself, plainly, not a neighbouring subject.
`pitch_nudge` no longer says "if you haven't told them what you do, say it in a sentence first": in a live check
the guide took it as a reminder and explained itself a second time inside the pitch (20_meeting.md still covers a
guide that never said it).
End demo (Ian, 2026-11-18: "a more prominent button ... that says end demo and visible while the onboarding and
chapter is being read, not only after. The transition should happen by default"): the button replaced I'm done and
shows through the conversation too, so the handback has a note for each place it can be tapped with the guide
there to say goodbye: `handback_done` while the chapter is read or asked about, `handback_talk` in conversation,
`handback_making` in conversation while their chapter is being made (End demo now stops it: below). Those two and
`handback_end` go as speakable context, so they have no brackets (the page adds them when it asks once more as an
instruction): sent as instructions, live, the guide said nothing (in conversation it had just asked a question
and went on waiting for the answer; at the chapter's end it had finished reading), and the debrief followed 8 to
12 s of silence. `handback_done` stays an instruction: it has a reading to stop, as `tapped` does. With no session
open (resting, paused) nothing is said: the debrief starts at once.
Ian's third tester run (2026-11-19): `offer_nudge` (with config live.director on, only as the first late fallback
when nothing was explained: the earpiece reads the conversation instead) cut into his story 50 s in and said "No more questions about their day", dropped; the pitch
nudges are 720f7d5's short lines again (with the real-interest fallback of 2026-11-16, and without "if you haven't
told them what you do", which made it explain twice); `started_step_back` and `started_company` carry the topic
({topic}: start_chapter's topic as pitched), because he asked "What would it be about?" and never heard; and
`started_step_back` ends on a real question whose answer the page waits for (it had stepped back 6 s after "unless
there's anything else you'd like to chat about"). End demo now stops a chapter still being made ("We definately
need to stop the run of the chapter once the post inverview starts"), so `handback_making` no longer promises it.
`direct`, `direct_now` and `direct_start` wrap the earpiece's whisper (voice/gpt_live/director.py, config
live.director; Ian, 2026-11-19: "an earpiece ... with a plan") as an instruction, sent at once with the mic open.
GPT-Live takes any instruction as a task for its very next turn (scratch earpiece/EVIDENCE.md): "now" and "say"
made notes speak within 1 to 2 s, and heavily hedged notes went unsaid for minutes, so `direct` (kind
when_it_fits, almost every whisper) has neither word and gives it leave to skip; the earpiece itself times the
send. `direct_now` is for the rare must-happen note (a clear yes the guide moved past, quiet asked for and not
given), `direct_start` for a "now" note about starting, so a clear yes starts it through start_chapter without a
word about it. No append can be withdrawn and old notes stay active, so each whisper replaces the last, and
`started_step_back` and `started_company` end the earpiece's last one ("Any earlier earpiece note is done.", which
director.CLEARED reads back). director.whisper_in matches the three wrappers around {note}: keep it the only
placeholder in them. On a no to
`started_step_back`'s offer to chat, the guide is told to use step_back: the page reads the no too (its main path),
and the tool is a second one if the page's patterns miss the words.
Placeholders in single braces ({title}, {text}, {when}, {topic}) are filled by the page.
---
opened: Say hello now: only a bright, glad hello of two to four words, in your own words, different every time. Nothing else yet: no name, no question. Then wait for them.
read: Read this passage aloud now, word for word, exactly as written, with nothing before or after:
tapped: [The listener tapped to ask something: the chapter is paused. Stop reading now, even mid-sentence, and listen. Any passage you were given and haven't finished is cancelled: never go on with it yourself; the app gives it to you again when the reading continues. If they say nothing, open in a word or two.]
context: [Silent context for the listener's question, from the app. Do not read it aloud.]
back: [The listener tapped to go back to the chapter. Say nothing: the reading continues now.]
ready_again: [Their chapter is still waiting for them. At the next natural pause, ask once more, lightly, whether they'd like to hear it now. If they're in the middle of something, let it be.]
next_ready: [The next part of the chapter is ready, and the reading goes on as soon as you stop. Ask nothing new: say one short line that ties what they said to what is coming, and stop.]
check_in: [The reading has reached a part that is still being written, about {when}. Come in now, in your own words, as your check-in rules say.{earlier}]
checkin_silence: [No answer for a while. A word or two that you'll pick it up when the next part is ready, then wait quietly.]
silence: [A few seconds of silence since you spoke. If you have answered their question, lead back in a few words and go back to the chapter now. If you haven't answered it yet, answer it now instead.]
unanswered: [They asked you something and you haven't answered it. Answer it now yourself, in one to three short sentences, from the chapter or what you know; if you don't know, say so plainly. Don't say you'll think about it.]
wake: [The listener tapped to bring you back. Let them know you are here in a word or two, and wait.]
continue: This session carries on the same conversation and the same chapter. Don't greet or say anything now: the app sends the next passage of the chapter to read straight away.
returned: This session carries on the same conversation after the connection dropped. Let the listener know you are back in a word or two, and wait.
typed: [The listener typed this instead of speaking: "{text}"]
opening_words: Say these exact words now, then listen: "{words}"
reopened: This session picks up a conversation already under way: the listener reopened the page while their chapter "{title}" is being made. Say you're back in a few words, then keep them company until it's ready, as your rules for that time say.
reloaded: This session picks up a conversation and a chapter already under way: the listener reopened the page while "{title}" was being read. Say you're back in a few words, nothing more: the app goes on with the reading straight after you.
start_nudge: [If you just told them you're starting their chapter, it hasn't started: Generate <|SpawnThinking|> to call start_chapter now, saying nothing about it. If you didn't, ignore this.]
chapter_stopped: [The rest of their chapter couldn't be written: it stopped. Say so plainly in a sentence, without guessing why, and keep them company.]
chapter_halted: [The listener stopped their chapter, so this is as far as it was written. Say so in a few words and keep them company.]
chapter_failed: [Their chapter couldn't be made: it stopped before the opening was written. Say so plainly in a sentence, without guessing why; the page's Try again button can carry on with it. Don't start another yourself.]
chapter_halted_early: [The listener stopped their chapter before it began. Acknowledge it in a few words and keep them company.]
ready_unheard: This session picks up a conversation already under way: the listener is back, and their chapter "{title}" is written but they haven't heard it yet. Say you're back in a few words, tell them it's ready and ask if they'd like to hear it now. Start it only when they say yes (start_reading).
hello_silence: [They haven't said anything since your hello. A light word or two, like asking if they can hear you, then wait.]
offer_nudge: [A quiet moment, or you've chatted a little while: your cue to take the initiative. If they don't know yet what you do, offer to tell them now, in one short, eager question like "Want to hear what I do?" (if they're mid-thought, let them finish and react to it first). If they already know, ask what they've been listening to, reading or watching lately instead (one of them, not a list).]
pitch_nudge: [You've been talking a few minutes. At the next natural pause, not mid-thought, move toward their chapter: pitch the thing that has interested them most, or, if nothing has stood out, ask which of the things they've mentioned they'd want to hear more about.]
pitch_nudge_again: [It's time to bring up their chapter, unless you just have: at the next pause, pitch one idea from what has interested them most.]
moved: [The listener moved the chapter to another place. Stop now, even mid-sentence, and say nothing: the next passage comes straight away.]
offer_line: Your chapter's ready: "{title}". Want to hear it now?
ready_line_resting: Your chapter's ready whenever you are. Tap play when you'd like it.
asking: This session carries on the same conversation. The chapter is paused and the listener is about to ask you something: listen, and answer as your rules for questions say.
started_step_back: [Their chapter is under way: "{topic}". The opening should be ready in {when}. In a sentence or two, in your own words: say so and what it will be about, in a few words; that you'll step back for a bit while you research it, and they can speak up or tap your circle any time; and ask, lightly, whether there's anything else they'd like to chat about first. Then wait for their answer: if they'd like to chat, keep them company; if not, a few words, then use the backend tool step_back and say nothing more. Any earlier earpiece note is done.]
started_company: [Their chapter is under way: "{topic}". The opening should be ready in {when}. They've been enjoying the chat: say in a sentence what it will be about, that you'll research it in the background and you're happy to keep talking, or to step back if they'd rather have quiet. Then carry on the conversation. Any earlier earpiece note is done.]
back_from_rest: This session carries on the same conversation: you had stepped back while their chapter was made, and the listener has tapped to talk. Let them know you're here in a word or two, and listen.
listening_again: This session carries on the same conversation: you had stepped back while their chapter was made, and the listener may be about to speak to you. Say nothing first: listen, and answer only if they talk to you.
ready_back: This session carries on the same conversation: you had stepped back while their chapter was made, and it's ready now. Say nothing until the app gives you the words to say.
handoff: This session picks up from the person who welcomed them; they just told them their name is {name}. Say hello to {name} by name, brightly, glad to meet them, in a few words, and give your name. Don't ask their name again.
handback_end: The chapter has ended and nothing more is to be read. Speak now, in your own words: close it in a sentence or two, then say you're handing them back to the person who welcomed them, and stop.
handback_done: [The listener tapped End demo, so the chapter stops here. Stop reading now, even mid-sentence: never go on with the passage. Close in a sentence or two, then say you're handing them back to the person who welcomed them, and stop.]
handback_talk: The listener tapped End demo, so the conversation ends here. Speak now, without waiting for them: close warmly in one sentence, then say you're handing them back to the person who welcomed them, and stop. Ask nothing more.
handback_making: The listener tapped End demo, so the conversation ends here, and their chapter, still being made, stops with it. Speak now, without waiting for them: close warmly in one sentence, without promising them the chapter; then say you're handing them back to the person who welcomed them, and stop. Ask nothing more.
direct: [Earpiece note from your producer, for you only: {note} Work it in your own words when that moment comes, never over them. If it doesn't come, let it go. This replaces any earlier earpiece note.]
direct_now: [Earpiece, this one matters: {note} At your next turn, never over them. This replaces any earlier earpiece note.]
direct_start: [Earpiece, this one matters: {note} If they have clearly said yes, start it now: Generate <|SpawnThinking|> to call start_chapter, saying nothing about it. This replaces any earlier earpiece note.]
