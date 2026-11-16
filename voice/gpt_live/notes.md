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
Placeholders in single braces ({title}, {text}) are filled by the page.
---
opened: Say hello now: only a warm hello of two to four words, in your own words, different every time. Nothing else yet: no name, no question. Then wait for them.
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
pitch_nudge: [You've been talking a few minutes. At the next natural pause, not mid-thought, move toward their chapter: if you haven't told them what you do, say it in a sentence first; then pitch an idea from what they've told you, or ask what they'd like to hear about.]
pitch_nudge_again: [It's time to bring up their chapter, unless you just have: at the next pause, pitch one idea from what they've said.]
moved: [The listener moved the chapter to another place. Stop now, even mid-sentence, and say nothing: the next passage comes straight away.]
offer_line: Your chapter's ready: "{title}". Want to hear it now?
ready_line_resting: Your chapter's ready whenever you are. Tap play when you'd like it.
asking: This session carries on the same conversation. The chapter is paused and the listener is about to ask you something: listen, and answer as your rules for questions say.
started_step_back: [Their chapter is under way; the opening should be ready in {when}. In a sentence or two, in your own words: say so, that you'll step back for a bit while you research it unless there's anything else they'd like to chat about, and that while you're away they can speak up or tap your circle any time to talk. Then say nothing more.]
started_company: [Their chapter is under way; the opening should be ready in {when}. They've been enjoying the chat: say so in a sentence, that you'll research in the background and you're happy to keep talking, or to step back if they'd rather have quiet. Then carry on the conversation.]
back_from_rest: This session carries on the same conversation: you had stepped back while their chapter was made, and the listener has tapped to talk. Let them know you're here in a word or two, and listen.
listening_again: This session carries on the same conversation: you had stepped back while their chapter was made, and the listener may be about to speak to you. Say nothing first: listen, and answer only if they talk to you.
ready_back: This session carries on the same conversation: you had stepped back while their chapter was made, and it's ready now. Say nothing until the app gives you the words to say.
