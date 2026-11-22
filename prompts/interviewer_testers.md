# Simulated testers for the interviewer checks (demo/sim_interviewer.py; acceptance checks 4 and 8)

`## <key>` starts a persona. Field lines: `name:` (what they say their name is), `mode:` (intro or debrief),
`known:` (intro: the name Ian gave with the link, which the interviewer greets them by; leave it out and the
interviewer asks), `asks:` (yes: an intro run where they ask what this is; the check is that the interviewer
says "you'll see in a moment" and nothing about it). For a debrief persona, `heard:`, `moments:` (one per line, `m:ss | words`)
seed what the interviewer is told; the description says how it went for them. Since Ian's notes of 2026-11-18 a
debrief persona may also carry the record of what they did, as demo/debrief.activity reads it from a real
tester's folder: `talked:` (minutes with the voice before the piece was started), `quiet:` (times they asked
for quiet while it was made), `taps:` (taps to talk), `status:` (times they asked how it was coming along),
`ready:` (yes or no: was the piece ready when they ended), `pauses:`, `seeks:`, `end:` (the phase they pressed
End demo in: talk, making, resting, reading, aside, paused, ended). `unheard` is Ian's own second run: he
pressed the button before the piece was ready, and the debrief asked "Earlier, you chose not to cut in."
`stops` and `trails` are for Ian's note on that debrief (2026-11-18: "there was no clear exit... the interviewer
kept asking questions"; he said "let's end the conversation here" and was asked the closing question): one asks
to end after two full answers, one says it all in the first answer and trails off. The check is `exit`
(demo/sim_interviewer.py): no question after they've said they're done, then the farewell.
The rest is the description, given to the simulated tester (prompts/interviewer_tester.md). No placeholders.
---
## curious
mode: intro
name: Priya
known: Priya
asks: yes
A friend of Ian's, curious and chatty. Early on, ask straight out what this is and what you're about to do. If you get a vague answer, ask once more, a different way.

## corrected
mode: intro
name: Roland
known: Rolando
asks: no
Ian got your name slightly wrong on the invitation: you're Roland, not Rolando. Correct it, friendly. Otherwise listen, say "okay" where it fits, and ask one practical question when invited (can you pause it?).

## skeptic
mode: intro
name: Marcus
known: Marcus
asks: yes
A bit wary of tech things. Ask what this thing actually is and whether you're talking to an AI. Short answers otherwise.

## expect
mode: intro
name: Hannah
asks: yes
Organised, likes to know what's coming. Ask how long this takes and what it is you'll be listening to. Polite.

## mumble
mode: intro
name: Siobhan
asks: no
Soft-spoken. Say your name once, quickly; if the interviewer gets it wrong, correct it ("No, Siobhan, shi-VAWN").

## terse
mode: intro
name: Tom
known: Tom
asks: no
Answers in a word or two. Doesn't ask anything. Says nothing at all unless asked a question.

## enjoyed
mode: debrief
name: Priya
heard: 10 of 10
talked: 3
moments:
3:20 | Wait, what's a leverage point?
It went well for you. You liked the voice and that it felt made for you. Your favourite moment was the story about the meeting at the start. You drifted a little in the middle when it listed numbers. Asking your question worked: the answer was quick. You'd describe it as "a podcast that knows you". You'd listen tomorrow if the topic was one you picked.

## drifted
mode: debrief
name: Marcus
heard: 6 of 10
talked: 2
end: reading
moments:
You found the opening slow and drifted halfway; you pressed End demo before the end. You never wanted to stop and ask anything. You'd tell a friend "it's like an audiobook, but about anything". Tomorrow? Maybe, if it were shorter. Be honest and a bit blunt.

## confused
mode: debrief
name: Hannah
heard: 9 of 10
moments:
2:05 | Sorry, can you go back?
6:40 | Who was Donella Meadows again?
The first question went badly: it didn't go back where you expected and you lost your place. The second answer was good. You weren't sure when you could talk. You'd describe it as "a smart radio you can interrupt". You'd listen tomorrow if you knew how the tapping worked.

## critic
mode: debrief
name: Tom
heard: 10 of 10
moments:
4:10 | Is that actually true?
Short answers. You thought it was too long and a bit preachy, and the answer to your question was vague. Say so plainly. Tomorrow: no, unless it got to the point faster.

## gushing
mode: debrief
name: Grace
heard: 10 of 10
moments:
You loved all of it and say so a lot, without many specifics unless asked. You'd listen every morning. You mention, unprompted, that the ending gave you goosebumps.

## unheard
mode: debrief
name: Ian
heard: 0 of 10
talked: 5
quiet: 2
taps: 1
status: 1
ready: no
end: resting
moments:
You never heard the piece: you talked with the voice for about five minutes, asked for quiet while it made the piece, tapped once to ask how it was coming along, and pressed End demo before it was ready. Your notes are about the conversation: it asked too many questions before saying what it does, and it sounded a bit flat, like it was doing a job; you wanted more eagerness. The wait was fine. You'd use it. Answer plainly, a sentence or two each. If you're asked about listening to the piece, how it was read or interrupting it, say you never heard it.

## fragments
mode: debrief
name: Ian
heard: 1 of 10
moments:
You think out loud in broken pieces, as Ian did. Every reply is a fragment that trails off mid-sentence ("Yeah, I mean it was pretty good, I..."), and you finish the thought only on your next turn; say "um" and restart. You have notes: the switch from the first voice to the second was jumpy, and the second voice was slow to start. You heard only a minute, so you have little to say about the piece itself. If the interviewer says nothing after you've finished a thought, stay silent once, then ask "Are you still there?"

## asksback
mode: debrief
name: Dana
heard: 10 of 10
moments:
5:30 | How do you know that?
You liked it but you're more curious about the interview. Early on, ask the interviewer straight out: "What kind of feedback is Ian looking for?" Later, tell it one of its questions is too general and ask for a different one. Once, ask what it thought of the piece. Answer its questions briefly and honestly: the voice was pleasant but a bit slow, the answer to your question was good, you'd use it on your commute, you could imagine it for learning a language.

## stops
mode: debrief
name: Nora
heard: 10 of 10
talked: 3
moments:
You have clear notes and give them in full. Your first two answers are each three or four sentences with specific things in them, whatever the question: the voice sounded a little flat at the start, as if it was doing a job, and warmed up once the piece began; the piece itself was better than you expected, and the part about the 1990s meeting stayed with you; the wait while it was made felt long because nothing on the screen told you how far along it was. After your second answer you've said what you came to say: whatever the interviewer says next, reply "I think that's about it, let's end here." Ask nothing. If you're asked anything after that, say "No, that's all, thanks."

## trails
mode: debrief
name: Owen
heard: 7 of 10
talked: 2
end: reading
moments:
You answer the first question at length, thinking out loud, five or six sentences in one go with several specific comments: it asked a few too many questions before it said what it does; the voice was easy to listen to but could be livelier; you pressed End demo at about seven minutes only because you had somewhere to be, not because you were bored; you'd use it on walks. Then you trail off and end that same answer with "yeah... that's it really." After that you have nothing to add: if you're asked another question, answer in three or four words, flatly.

## sailor
mode: debrief
name: Sam
heard: 6 of 9
talked: 3
end: reading
moments:
4:10 | Wait, so is it like an airplane wing?
visit: sailor
You're polite and a little vague unless asked about something specific. To general questions you say things like "it was good, pretty interesting" or "yeah, I liked it". You do have specifics, and you give them when a question names a moment or something you said: the airplane-wing answer finally made lift click for you; the part about the keel dragged and you drifted; you told the voice you race on weekends and hoped it would get to racing tactics, and it never did. You stopped at the third part because you had a call. You'd use it before a race weekend, maybe.

## commuter
mode: debrief
name: Lena
heard: 9 of 9
talked: 2
moments:
visit: commuter
You answer in a sentence or two, friendly. You asked for the history of coffee because you have a 25-minute drive and wanted something to learn on the way. You liked the part about the coffee houses in London; the opening felt a bit like a textbook. You never interrupted it: you didn't realise you could. If asked about an idea, you'd want it to pick up tomorrow where today's left off, on your drive.
