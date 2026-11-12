# Research output format (a fragment, not a prompt of its own)

Inputs:
- {{row_id}}: the ledger row, e.g. L01
- {{start}}: the first entry number (1, or after the earlier agent's entries in a follow-up)

Inserted as {{format}} into research_row.md and research_verify.md. This is a
parser contract: stages/research.py reads the <research> block, the
'## Entries' section, each '[L01.3] (kind, tier N, ...)' line and the
'## Not found' section. The merge renumbers entries D01, D02, ... by code and
keeps the tags, so change the format only together with research.parse_entries.
---
The fetch tool only opens a URL that has already appeared in a search result or in this prompt, so search for a page before you fetch it; never type a URL from memory or by pattern. Some hosts refuse the fetch tool: wikisource.org and sacred-texts.com so far. For a primary text, search for it on Project Gutenberg, Perseus or classics.mit.edu, and fetch the URL the search returns. If a fetch fails, fetch the same text from another host before you write: a quote seen only in a search result is never verbatim.

When your research is done, write what you found inside <research> tags, exactly in this shape:

<research>
# {{row_id}}: the idea, as given above

## Summary
Two to four sentences: what you established, whether the question is answered, and at what tier.

## Entries
[{{row_id}}.{{start}}] (claim, tier 2) One fact, in one or two plain sentences. Source: page title, URL, section or page.
[{{row_id}}.next] (material, tier 3) A concrete scene, story, example or image, told plainly enough that a writer could retell it: who, where, when, what happened. Source: page title, URL.
[{{row_id}}.next] (quote, tier 1, verbatim: yes) "Words copied character for character from a page you fetched." Who wrote or said them, the work, the passage reference (Stephanus number, book and chapter, page), the translator. Source: page title, URL.
[{{row_id}}.next] (tension, tier 2) The strongest objection to the idea, or the complication it has to survive, and who makes it. Source: page title, URL.
[{{row_id}}.next] (connection, L03) One line on how this idea links to row L03's idea.

## Not found
What you looked for and did not find, or found only below the tier needed. Write "Nothing." if there is nothing to report.
</research>

Number the entries [{{row_id}}.{{start}}], then the next number, and so on, each at the start of its own line. "next" above stands for that number.

The tags in parentheses come right after the number:
- The kind first: claim, material, quote, tension or connection.
- Then the source's tier, 1 to 4, from the table above, or "tier episode" for something taken from the episode. Every entry except a connection has one. The tier is that of the page you cite: a Gutenberg text of Plato is tier 1; a passage of Plato quoted in an encyclopedia entry is tier 2, the encyclopedia's tier; a blog post about Plato is tier 4. To claim tier 1, fetch and cite the primary text itself.
- Quotes only: "verbatim: yes" if you copied the words character for character from a page you fetched in this research, and the entry names that page's URL; or, for the episode, if you copied a line word for word from the map's short quotable lines, with "Source: episode" (it is checked against the transcript). Anything you recall, reconstruct, translate yourself or take from a search snippet is "verbatim: no", and is written as a paraphrase, without quotation marks. Every verbatim: yes quote is checked word for word against its page; one that is not on it is downgraded, and the script cannot use it.
- Put only the quoted words in quotation marks. Give titles of works in plain text, never in quotation marks, so the check reads only the quote.
- A connection names the other row or rows it links to, and needs no tier or source.

Rules:
- Cite only URLs that a search returned or that you fetched. Never write a URL from memory.
- One fact per claim, small enough to check in a minute. Quotes under 40 words, in straight double quotation marks.
- Something only the episode says ends "Source: episode".
- If sources disagree, give each its own entry, and say in the second that it conflicts with the first, citing it by number.
