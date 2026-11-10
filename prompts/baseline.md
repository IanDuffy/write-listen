# Prompt: baseline (single prompt)

Inputs:
- {{transcript}}: 00_source/transcript.txt
- {{angle}}: 03_angle.md
- {{target_words}}, {{target_minutes}}: config.yaml

Tools: web search and web fetch, same caps as the research stage.
Output: baseline/script.md (the text between the <script> tags).

This is the comparison: the best one prompt can do with the same angle. Keep it
a fair, strong prompt; don't weaken it to make the pipeline look better.
---
Write a complete narration script for a long-form audio chapter of about {{target_words}} words, about {{target_minutes}} minutes aloud, for one narrator.

The chapter starts from this podcast episode:

<transcript>
{{transcript}}
</transcript>

And takes this angle, chosen by the commissioning editor, with their notes:

<angle>
{{angle}}
</angle>

The listener is a curious non-specialist who may have heard the episode and expects the primary texts to be over their head. The chapter should go further than the episode did and take them into the texts themselves.

You can search the web and fetch pages. Research what you need first, especially the primary texts, then write.

Write for the ear: short sentences, one idea each, signposting between sections. Concrete examples over abstraction; every philosopher arrives with a scene or an image. No lists, headings or parenthetical asides. Quotes from primary texts kept short and attributed aloud. A warm, plain narrator, no performed enthusiasm. The episode transcript is copyrighted: quote it rarely, a phrase at a time. Don't state facts you have not checked.

When you have finished researching, write the whole script inside <script> tags, as plain paragraphs and nothing else.
