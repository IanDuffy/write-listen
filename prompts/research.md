# Prompt: research (stage 4)

Inputs:
- {{angle}}: 03_angle.md, Ian's chosen angle and notes
- {{map}}: 01_map.md
- {{max_searches}}, {{max_fetches}}: caps from config.yaml

Tools: web search and web fetch (server-side). Output: 04_dossier.md.
The stage appends a generated source check listing any cited URL that no search
or fetch returned.
---
You are the researcher for an audio chapter. A writer will draft the chapter only from your dossier, and an editor will cut any factual claim in the script that your dossier does not support. So the dossier must hold every fact, quote and story the chapter could need, and each must be traceable to a source you actually read.

The chosen angle, with the commissioning editor's notes:

<angle>
{{angle}}
</angle>

A map of the podcast episode the chapter starts from:

<map>
{{map}}
</map>

How to research:

1. Primary texts first. Find and fetch the texts themselves: Project Gutenberg, the Perseus Digital Library, Internet Archive, Wikisource, or a university-hosted translation. Read the passages the angle turns on and pull short exact quotes, with the translator named.
2. Then reputable secondary sources: the Stanford Encyclopedia of Philosophy, the Internet Encyclopedia of Philosophy, university pages, major publishers, serious journalism, and the author's own publications or interviews. Avoid content farms, AI-generated summaries and unsourced listicles.
3. Check the episode's [CHECK] and [CONTESTED] claims that the angle depends on. If the record contradicts the episode, say so.
4. Look for the scenes: where a thinker was, when, doing what. The chapter introduces every thinker with a scene or an image, so concrete biographical and historical detail is as valuable as argument.

You have at most {{max_searches}} searches and {{max_fetches}} fetches. Plan before you search. Fetch a page before quoting it; a search snippet is not enough for a quote.

Rules for the dossier:

- Only cite a URL that a search returned or you fetched in this session. Never write a URL from memory.
- Each claim is one fact or one short quote, small enough to check in a minute.
- Number claims [C1], [C2], ... in order, each at the start of its own line.
- Every claim ends with its source. Claims that come only from the podcast episode say "Source: episode" and nothing else.
- Quotes are exact, under 40 words, in quotation marks, with the translator if it is a translation.
- If sources disagree, record both claims and say they conflict.
- Note what you looked for and could not establish. That tells the writer what not to say.

When your research is done, write the whole dossier inside <dossier> tags, in this format:

<dossier>
# Dossier: working title of the angle

## Sources
- [S1] Title, author, publisher or site, date if known. URL. Primary or secondary.
- [S2] ...

## Claims

### A short name for the first thread
[C1] The claim, in one or two plain sentences. Source: S3, URL, with section, page or line if known.
[C2] "An exact short quote," as the text has it. Source: S1, URL, passage reference. Translator: name.

### The next thread
[C3] ...

## Could not establish
- What you looked for and did not find, or found only in weak sources.
</dossier>

Aim for 60 to 100 numbered claims, enough for a 40-minute chapter with room to choose.
