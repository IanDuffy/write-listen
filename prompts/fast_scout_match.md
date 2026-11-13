# Prompt: fast scout match (Fast Start step 0, deciding whether an early scout is reused)

During the conversation the guide may scout a likely topic before the listener
confirms one (scout_topic). When the chapter starts, code first compares the
confirmed topic with each scouted topic after normalising; only if none is equal
does this one cheap call decide. A wrong "yes" opens the chapter on the wrong
subject, so the prompt leans to 0.

Inputs:
- {{seed}}: 00_source/seed.md, the confirmed topic and what the listener said
- {{topics}}: the scouted topics, numbered from 1, one per line

Output: one number and nothing else: the matching topic, or 0. Code reads any other reply as 0
(scout._ask_match), and offers only the topics scouted in this conversation (scout._early_scouts).
---
A listener has asked for an audio chapter. This is the topic they settled on, and what they said:

<seed>
{{seed}}
</seed>

Earlier in the conversation, before they settled, these topics were scouted: a few web searches each, to find the sources and the scene a chapter on that topic would open with.

<scouted>
{{topics}}
</scouted>

Is one of them the same subject as the chapter they asked for, worded differently, so that its sources and its opening scene are the ones this chapter needs?

A broader subject, a narrower one, or a neighbouring one is not the same: scouting done for "Roman engineering" does not serve a chapter on "why Roman concrete lasts", and scouting done for "concrete" does not either. If you are unsure, it is not the same.

Answer with the number of the matching topic, or 0 if there is none. The number only.
