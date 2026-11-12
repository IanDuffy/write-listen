# Research output format for seed runs (a fragment, not a prompt of its own)

The seed-run variant of research_format.md, inserted as {{format}} into
research_row_seed.md and research_verify_seed.md. Same parser contract
(research.parse_entries); only the episode rules are gone, since a seed run has
no episode. Keep the two files in step.
---
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
- Then the source's tier, 1 to 4, from the table above, Every entry except a connection has one. The tier is that of the page you cite: a Gutenberg text of Plato is tier 1; a passage of Plato quoted in an encyclopedia entry is tier 2, the encyclopedia's tier; a blog post about Plato is tier 4. To claim tier 1, fetch and cite the primary text itself.
- Quotes only: "verbatim: yes" if you copied the words character for character from a page you fetched in this research, and the entry names that page's URL. Anything you recall, reconstruct, translate yourself or take from a search snippet is "verbatim: no", and is written as a paraphrase, without quotation marks. Every verbatim: yes quote is checked word for word against its page; one that is not on it is downgraded, and the script cannot use it.
- Put only the quoted words in quotation marks. Give titles of works in plain text, never in quotation marks, so the check reads only the quote.
- A connection names the other row or rows it links to, and needs no tier or source.

Rules:
- Cite only URLs that a search returned or that you fetched. Never write a URL from memory.
- One fact per claim, small enough to check in a minute. Quotes under 40 words, in straight double quotation marks.
- If sources disagree, give each its own entry, and say in the second that it conflicts with the first, citing it by number.
