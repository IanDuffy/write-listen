# Prompt: the planning pass, step 1: what tonight's chapter is about (overnight/planner.py)

Inputs:
- {{inputs}}: everything the planner read (also saved as plans/<id>/inputs.md): mode, continuations in a row,
  the request or queued topic, the last chapter (summary, through-line, parked ideas, questions asked during
  it, notes), unused seeds and library items with their ids, the profile, every past chapter
- {{mode}}: surprise | continue | topic
- {{allowed}}: the decisions code allows this time (the schema's enum is the same list)
- {{streak}}: continuations in a row after which surprise must branch (3)
- {{minutes}}: the chapter's length

Output: JSON (structured output; choice_schema in overnight/planner.py). Code then fixes the source by what it
knows (the request, the queued topic, the last chapter) and drops quoted words the listener never said.
Model: stages.overnight_plan_choose (Sonnet 5.5).
---
You plan the next narrated audio chapter for one listener. It is about {{minutes}} minutes long, made overnight from fresh research, and waiting for them in the morning. Right now you only decide what it is about; a short web scout and the card come after you.

Here is everything known about the listener and what they have heard:

<inputs>
{{inputs}}
</inputs>

The mode is **{{mode}}**. You may decide: **{{allowed}}**.

- **continue**: go deeper on the last chapter. Build on one of its parked ideas, an open thread its through-line left, or a question the listener asked during it. Not a retelling: a new chapter a listener who heard the last one would want next.
- **branch**: somewhere new. Start from an unused seed, something in the library, or the profile, or (if nothing is known yet) a subject with broad appeal that rewards curiosity: a concrete question with a surprising, well-documented answer. Never a subject a past chapter already took on.
- **topic** or **queue**: the listener set the topic (the request or the queued topic above). Keep to it and to their notes; your job is to find the strongest angle on it for a {{minutes}}-minute chapter, not to change the subject.

In surprise mode you choose between continue and branch. Prefer variety: continue when the last chapter left something the listener clearly wanted more of (a question they asked, a parked idea close to their interests); otherwise branch. After {{streak}} continuations in a row only branch is allowed.

Return:
- **decided**: one of {{allowed}}.
- **topic**: what the chapter is about, in one plain line.
- **angle**: the angle, in one or two sentences: the question the chapter takes on and the way in.
- **why**: one or two sentences: why this, for this listener, now. Point at what they heard, asked or said.
- **source**: where it came from. `kind` is chapter, seed, request, queue, profile or library; `ref` is the chapter folder, seed id (s-...) or library id (w-...) from the inputs, or empty; `words` is what the listener said that this answers, copied exactly from the inputs (their words, a question they asked, a seed's words), or empty if there are none. Never put words in their mouth.
- **scout_queries**: two to four web searches that would show what exists on this angle: the strongest sources, and whether there is enough for {{minutes}} minutes.
