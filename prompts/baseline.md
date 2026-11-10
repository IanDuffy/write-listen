# Prompt: baseline (single prompt)

Inputs:
- {{transcript}}: 00_source/transcript.txt
- {{angle}}: 03_angle.md
- {{style_guide}}: prompts/style_guide.md, the same house style the pipeline uses
- {{target_words}}, {{target_minutes}}: config.yaml

Tools: web search and web fetch, with the baseline caps in config.yaml
({{searches}} searches, {{fetches}} fetches).
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

You can search the web and fetch pages: about {{searches}} searches and {{fetches}} page fetches in total, so plan before you search. Research what you need first, especially the primary texts, then write.

Write in this house style:

<style_guide>
{{style_guide}}
</style_guide>

The episode transcript is copyrighted: quote it rarely, a phrase at a time. Don't state facts you have not checked.

When you have finished researching, write the whole script inside <script> tags, as plain paragraphs and nothing else.
