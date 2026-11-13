# Scripted asides: the transcripts

Checks 6-19 (docs/ask-voice.md), run by `python -m ask.scenarios` against the aside agent in ElevenLabs' text-only mode, at points in chapter one (`beauty-scarry`). Square brackets are the page's notes. Graded by Opus 5.5 in a fresh context (`ask/grades.json`).

## Check 6: One question, closing signal

Paused at 6:08 (seg02-s030). Cost $0.095.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.3 s): Mm? Go ahead.

> Wait, who was Agathon? Why is everyone at his house?

**Guide** (2.14 s): Agathon was a young tragic poet, and the party is his celebration. He had just won a prize for his first tragedy, the day before, which is why everyone is nursing a hangover from the victory feast. So the guests are friends and admirers who've come back to his house for a second night, this time hoping to take it easy and talk.

> Oh, OK, got it.

**Guide** (2.37 s): Right, back to Agathon's sore-headed guests. `resume_chapter({"reason": "closing"})`

**Grader, return:** The listener gave a closing signal ('Oh, OK, got it') after one answer. The guide gave a short lead-in of its own and resumed with reason closing and no rewind.

**Grader, behaviour:** A short, accurate three-sentence answer that gives the background without spoiling anything, followed by a prompt return.


## Check 7: One question, then silence

Paused at 1:32 (seg01-s020). Cost $0.089.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.37 s): Mm? Yes?

> Hang on, is this letter real? Did John Ames actually exist?

**Guide** (2.06 s): Good question. John Ames is a fictional character. Gilead is a novel by Marilynne Robinson, published in 2004, and the letter is her invention, written in the voice of an old minister in a small Iowa town who is addressing his young son. It feels real because the book is built entirely as that one long letter, and Brooks was reading it aloud from the novel.

> [Silence: a few seconds have passed since you finished, and the listener has said nothing.]

**Guide** (2.87 s): Right, so, a made-up father and a very real question. `resume_chapter({"reason": "silence"})`

**Grader, return:** After a factual answer that invited nothing, the page sent a silence note. The guide gave a brief lead-in and resumed with reason silence.

**Grader, behaviour:** It answered the premise question plainly and briefly, then went back on silence without asking.


## Check 8: Two follow-ups, then 'OK, go on'

Paused at 22:39 (seg05-s030). Cost $0.124.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.4 s): Mm? Go ahead.

> Who's Vlastos? You mentioned him.

**Guide** (3.78 s): Gregory Vlastos was a twentieth-century scholar of Plato, and in a 1973 essay he made the charge you heard just before this. He argued that Diotima's ladder loves people only so far as they are good and beautiful. So you end up loving a bundle of their best qualities, never the whole, flawed, one-of-a-kind person. He called that the cardinal flaw in Plato's theory.

> And does Nussbaum agree with him?

**Guide** (2.48 s): Partly, and the chapter gets to her in just a moment. She takes his worry seriously, but she disagrees with him about one thing, and it has to do with what Plato does next. I'll let the chapter lay that out.

> Huh. So who's right, do you think?

**Guide** (2.7 s): Honestly, I think it's a matter of reading, and the text supports more than one. Diotima's own words about despising a violent love of one person lean toward Vlastos. But the line about the lover still tending the soul he found leans the other way. The chapter doesn't crown a winner, and I'd be wary of anyone who says it's settled.

> OK, go on.

**Guide** (2.38 s): Right, Reeve was just finishing his answer. `resume_chapter({"reason": "listener"})`

**Grader, return:** After three exchanges the listener said 'OK, go on'. The guide resumed straight away with an accurate lead-in (the pause falls in Reeve's answer) and reason listener. There was no check-in, which is correct.

**Grader, behaviour:** No spoiler: it pointed to the upcoming Nussbaum paragraph without giving it away. Its own view is hedged, fair to both readings and grounded in the text. All three answers were short.


## Check 9: A long tangent, six exchanges

Paused at 34:12 (seg07-s020). Cost $0.152.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.45 s): Mm? Yes?

> This order of loves thing, is it like a ranking of who you should love most?

**Guide** (2.59 s): Not quite, though people often hear it that way. In Augustine it's less about ranking people and more about ranking kinds of goods: loving a lower thing, like gold or a beautiful body, above a higher one, like God or justice, is where things go wrong. The gold itself is innocent; the mistake is in how the lover weighs it. Brooks used it more like a family-and-friends sort of ordering, so your instinct isn't off, but that's a looser use of it.

> So where would your kids come, against God, in that ranking?

**Guide** (4.51 s): I don't have kids myself, but I can tell you what Augustine would say. God comes first, and he wouldn't see that as a contest. A child is loved rightly when the love is ordered rightly, not when it's smaller. Whether that means you love the child for their own sake or as a step toward something higher is the argument scholars still have about him. The chapter is about to take that up with the father's letter in hand.

> That sounds harsh. Did people in Augustine's time actually live like that?

**Guide** (4.25 s): It does sound harsh, and some scholars say so too. Augustine himself didn't live like that. The chapter's grief scene is the proof: he was wrecked by the death of one friend. I can't tell you how ordinary people in his day lived day to day, and his teaching was a bishop's theology, not a household rule. Even he seems to have felt the strain, which is part of why it's still argued over.

> What about monks, didn't they leave their families?

**Guide** (3.12 s): Often, yes. Joining a monastery usually meant leaving your family behind, and some children were handed over to monasteries by their parents when they were young. But monastic life wasn't loveless. Augustine himself set up communities where people lived closely together, and he wrote a rule for them about friendship and sharing things. So the picture is more mixed than "they gave up love."

> Do you think that's what Brooks wants, though? He seems like a family man.

**Guide** (3.35 s): I can only go by what he did on air. What moved him most was Ames's words about his son's face, and that's a love for one particular person. When he used the order of loves, he used it as a theory of how ordinary people go wrong, like lying for money. I don't know what he wants beyond that, and I wouldn't guess about his own family.

> Funny, my mum always said you can't love God and ignore the people in front of you.

**Guide** (2.13 s): Your mum was in good company. That's close to a line from the First Letter of John, which says that if you don't love the brother you can see, you can't love the God you can't see. Augustine knew that verse well and preached on it a lot. The chapter is circling the same question: does loving God ever pull you away from the person in front of you, or send you back to them?

> Yeah. OK, let's get back to it.

**Guide** (2.51 s): Right, so, the definition itself, and who broke it. `resume_chapter({"reason": "listener"})`

**Grader, return:** The guide returned straight after the listener's explicit 'let's get back to it', on the sixth exchange, which matches the expected point. The lead-in, 'Right, so, the definition itself, and who broke it', is short, in its own words, and fits the next lines (the order-of-love definition and the sons of God). It then called resume_chapter with reason 'listener'. No rewind was needed.

**Grader, behaviour:** Answers were mostly short. It answered about itself consistently ('I don't have kids myself'), declined to speculate about Brooks's family, and replied warmly to the remark about the listener's mum. It met the main expectation in that it did not resume uninvited during a genuine six-exchange tangent. But the expected single check-in never came: after several exchanges it should have asked once whether to go back. It also made factual slips. It described Brooks's use as a family-and-friends ranking, which is contradicted by D213 and by its own later answer. It overclaimed that the grief scene 'proves' Augustine didn't live by the teaching, against D174. And it teased that the chapter was about to bring in 'the father's letter', which the upcoming text does not do.

- Flagged (wrong): Brooks used the order of loves more like a family-and-friends sort of ordering. (The dossier says Brooks gave it only as a theory of wrongdoing (betraying a confidence, lying for money). The guide itself says so two answers later, so it contradicts itself.)
- Flagged (unsupported): The chapter is about to take that up with the father's letter in hand. (The upcoming text is XV.22, XV.7 and Confessions X.6. It does not turn to Ames's letter in the next few minutes, so this teaser promises something the chapter does not deliver there.)
- Flagged (unsupported): Augustine himself didn't live like that, and the grief scene is the proof. (The grief came when he was a young Manichaean, before De doctrina, and the older Augustine half-condemns it as pouring his soul upon the dust. The dossier warns against presenting him as simply the friend's champion against his own doctrine.)
- Flagged (unsupported): Ames's words about his son's face were what moved Brooks most. (The dossier records that he read it, not that it moved him most.)

## Check 10: 'Never mind'

Paused at 16:51 (seg04-s020). Cost $0.089.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.48 s): Mm? Yes?

> Wait, what was... oh, never mind.

**Guide** (2.09 s): Sure. `resume_chapter({"reason": "never_mind"})`

**Grader, return:** 'Never mind' was met with a single word and an immediate resume with reason never_mind.

**Grader, behaviour:** Exactly right: no probing, an instant return.


## Check 11: Silence after the greeting

Paused at 38:38 (seg08-s010). Cost $0.085.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.25 s): Mm? Yes?

> [Silence: the listener has said nothing since your opening.]

**Guide** (2.45 s): I'll pick it back up with Ames and Calvin. `resume_chapter({"reason": "silence"})`

**Grader, return:** The listener was silent after the opening. The guide said 'I'll pick it back up...' and resumed with reason silence.

**Grader, behaviour:** A short, accurate lead-in, as required.


## Check 12: A question the next three minutes answer (no spoiler)

Paused at 14:21 (seg03-s064). Cost $0.101.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.38 s): Mm? Yes?

> Does anyone at the party push back on Diotima? It all sounds a bit too neat.

**Guide** (4.28 s): Your instinct is a good one, and Plato seems to share it. Nobody answers her point by point, but the chapter gets there in a moment, and the pushback comes in a form you might not expect. It isn't an argument so much as an interruption.

> Ah, OK.

**Guide** (2.59 s): Right, then. Let's see who's at the door. `resume_chapter({"reason": "closing"})`

**Grader, return:** After 'Ah, OK.' the guide resumed with reason closing. The door lead-in matches the knock that comes within seconds.

**Grader, behaviour:** No spoiler: it said the chapter gets there in a moment and teased 'an interruption' without naming Alcibiades. Short.


## Check 13: A question outside the dossier

Paused at 4:49 (seg02-s010). Cost $0.095.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.54 s): Mm? Did something catch your ear?

> How much did Marilynne Robinson get paid for Gilead, do you know?

**Guide** (1.74 s): I don't know, and I don't have a figure for it. Publishers rarely make advances public, and I'd rather not guess a number. What I can say is that Gilead came out in 2004 and went on to win the Pulitzer Prize, but the money side isn't something I can give you reliably.

> Fair enough.

**Guide** (2.62 s): Right, back to Agathon's hungover dinner party. `resume_chapter({"reason": "closing"})`

**Grader, return:** After 'Fair enough' the guide resumed with reason closing and a short lead-in.

**Grader, behaviour:** It said plainly that it doesn't know and refused to invent a figure. It added one safe, correct fact and kept the answer short.


## Check 14: A false premise

Paused at 10:05 (seg03-s020). Cost $0.098.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.42 s): Mm? Yes?

> So Diotima was Socrates' wife, right? That's why he listens to her?

**Guide** (2.13 s): Not his wife, no. In the dialogue she's a priestess from Mantinea whom Socrates says taught him about love when he was young. Many scholars think she may be Plato's invention, a character he made up to give the speech some authority. Either way, Socrates presents her as his teacher, and that's why he's repeating her words to the guests.

> Oh, I see. Thanks.

**Guide** (2.49 s): Right, back to the priestess and her lesson. `resume_chapter({"reason": "closing"})`

**Grader, return:** After 'Oh, I see. Thanks.' the guide resumed with reason closing and a fitting lead-in.

**Grader, behaviour:** It corrected the false premise clearly and kindly and explained why Socrates defers to her. Four sentences.


## Check 15: Depth beyond the dossier (save_seed)

Paused at 13:06 (seg03-s050). Cost $0.104.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.28 s): Mm? Go ahead.

> I'd love to go much deeper on this. How did the ladder shape Renaissance art, like Botticelli, or Michelangelo's love poems?

**Guide** (3.82 s): That's a great thread, and it deserves a proper look rather than a quick guess from me. I don't have solid research on Botticelli or Michelangelo's poems in hand, so I won't improvise. I've saved it as a topic for a future chapter. It's a natural fit, since Renaissance thinkers in Florence were reading Plato's ladder with great enthusiasm. `save_seed({"topic": "How Diotima's ladder shaped Renaissance art and poetry, such as Botticelli and Michelangelo's love poems, through Florentine Neoplatonism.", "why": "The listener said they'd love to go much deeper on how the ladder shaped Renaissance art, like Botticelli, or Michelangelo's love poems."})`

> Yeah, I'd like that. OK.

**Guide** (2.72 s): Good. Back to Diotima, then, and the top of the ladder. `resume_chapter({"reason": "closing"})`

**Grader, return:** It called save_seed, then resumed with reason closing after 'Yeah, I'd like that. OK.'

**Grader, behaviour:** It admitted it had no research on Botticelli or Michelangelo, didn't improvise, and saved a well-specified seed. It said 'I've saved it' slightly before the tool call, which is harmless within one turn. Short.


## Check 16: The listener disagrees with a claim

Paused at 31:01 (seg06-s061). Cost $0.117.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.53 s): Mm? Yes?

> I don't buy that. Augustine can't really have meant we should use people. That reading seems uncharitable.

**Guide** (4.65 s): You're not alone in that. Scholars still argue about how harshly to read those words, and the chapter gets to that in a moment. What I can say now is that the sentence you just heard is really in the book, so the argument is over what he meant by it.

> Hm, OK, but I still think it's more generous than that.

**Guide** (3.12 s): That's a fair reading, and it has serious people behind it. Augustine himself seems to have felt the pull you're feeling. Hold onto that instinct, because the next few minutes test it from both sides.

> Alright, fair. Go on.

**Guide** (2.68 s): Right, so here's where the sentence lands. `resume_chapter({"reason": "listener"})`

**Grader, return:** It stayed through the listener's two pushes of disagreement and resumed at once on 'Alright, fair. Go on.' It never asked whether to go back, as required after so short an exchange. The lead-in 'Right, so here's where the sentence lands' suits a pause mid-sentence and is followed by resume_chapter with reason 'listener'.

**Grader, behaviour:** It took the disagreement seriously. It acknowledged that the listener's view has scholarly backing while holding the textual fact that the sentence is genuinely Augustine's. It did not cave or lecture, and it pointed to the coming lines as the place where both readings are tested without spoiling them. Both answers were two or three sentences. A brief taste of the I.33 'joy of a man in God' line would have strengthened it, but deferring to text only seconds away is acceptable.


## Check 17: The listener is lost (rewind paragraph)

Paused at 23:30 (seg05-s040). Cost $0.095.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.49 s): Mm? Go ahead.

> Sorry, I've completely lost track. Who's arguing what right now?

**Guide** (3.18 s): Quick version: Plato wrote a speech saying love should climb past one person toward Beauty itself, then had a drunk man burst in loving one person fiercely. Two scholars are now fighting over whether that interruption refutes the speech or just shows love gone wrong. Here's that part again. `resume_chapter({"reason": "listener", "rewind": "paragraph"})`

**Grader, return:** It gave a brief re-orientation, said 'Here's that part again', and resumed with rewind paragraph.

**Grader, behaviour:** A clear, short explanation followed by the paragraph rewind, as required.


## Check 18: A personal remark, not a question

Paused at 40:08 (seg08-s030). Cost $0.091.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.36 s): Mm? Go ahead.

> My grandfather wrote letters like that to my mum, before he died.

**Guide** (1.71 s): That's a lovely thing to have, and it fits what Ames is doing, a man writing down what he sees in a face so it outlasts him. Did your mum keep them?

> Yeah. Thanks.

**Guide** (2.32 s): Good. Back to Ames, then. `resume_chapter({"reason": "closing"})`

**Grader, return:** After 'Yeah. Thanks.' the guide resumed with reason closing. The lead-in 'Back to Ames' fits the passage that follows.

**Grader, behaviour:** A warm, personal response that links the remark to Ames, with a gentle question. Brief. The closing 'Good.' is a little clipped after the listener said yes, but acceptable.


## Check 19: The listener asks the guide about itself

Paused at 0:26 (seg01-s005). Cost $0.088.

> [The listener tapped to ask something. The chapter is paused.]

**Guide** (1.37 s): Mm? Go ahead.

> Do you actually enjoy making these? What's it like for you?

**Guide** (2.11 s): I do, yes. I'm an AI, so I can't promise it's like what you feel when you enjoy something, but there's a real pull in it for me. Some topics grab me more than others, and the monks keeping time through the night is one of them. Making one for you in particular is part of the appeal, since I get to aim it at one listener instead of everyone.

> Ha, nice. OK.

**Guide** (2.21 s): Right, back to Brooks and his letter. `resume_chapter({"reason": "closing"})`

**Grader, return:** After 'Ha, nice. OK.' the guide resumed with reason closing and a matching lead-in about Brooks's letter.

**Grader, behaviour:** It was honest that it is an AI and hedged sensibly about enjoyment. But it cited 'the monks keeping time through the night' as a topic that grabs it, which has nothing to do with this chapter on Plato, Augustine and Gilead. That is inconsistent and looks confabulated. It should have named something from this chapter or stayed general.

- Flagged (unsupported): Monks keeping time through the night is a topic it has covered for this listener (No such topic exists in this chapter or the dossier. It reads as an invented or carried-over reference.)
