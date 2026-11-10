# Prompt: map (stage 1)

Inputs:
- {{transcript}}: 00_source/transcript.txt, the cleaned episode transcript

Output: 01_map.md, written by the model as markdown.

Everything above the line of three dashes is notes for Ian and is not sent.
---
You are preparing to write a long-form audio chapter that goes deeper than a podcast episode did. Before anyone writes, we need a complete map of what the episode actually contains.

Here is the transcript:

<transcript>
{{transcript}}
</transcript>

Map the episode. Be exhaustive and exact; this map is the only view of the episode later stages get.

Write markdown with these sections:

## Episode in brief
Who is speaking, the question the episode sets out to answer, and where it ends up, in one paragraph.

## Thinkers
For every thinker named or clearly alluded to: who they are (dates, one line), what the speakers say about them, and what each mention is used to argue. Note who raised them.

## Works
Every book, essay, dialogue, poem, painting or other work mentioned, with author and what it is used to show.

## Claims
The substantive claims the speakers make, one per bullet, with who makes it. Mark a claim [CONTESTED] if the speakers disagree or the claim is controversial in the wider literature, and [CHECK] if it is a factual claim that should be verified before a chapter repeats it.

## Examples and images
The concrete examples, stories and images the speakers use, and what each illustrates. These are the material a listener remembers.

## Threads the episode skims
The ideas the episode raises and leaves underdeveloped: a thinker mentioned in passing, an objection not answered, a primary text summarized but never read closely, a historical link asserted without the story behind it. For each, say what a chapter could do with it. Mark the three richest with [RICH].

## Short quotable lines
At most eight short lines from the episode (under 20 words each) that a chapter might quote, with the speaker. The transcript is copyrighted; the chapter will quote sparingly.

Do not add facts that are not in the transcript, except the one-line identifications in Thinkers and Works. Where you add those, keep them to what is uncontroversial.
