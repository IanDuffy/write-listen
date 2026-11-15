# Prompt: the planning pass, step 2: a quick scout (overnight/planner.py)

Inputs:
- {{topic}}, {{angle}}: from step 1
- {{queries}}: step 1's suggested searches, one per line
- {{searches}}: the web search budget (config overnight.plan.scout_searches); no page fetching
- {{minutes}}: the chapter's length

Output: plain notes (plans/<id>/scout.md; code appends the sources the searches returned). No deep research:
the overnight build does that.
Model: stages.overnight_plan_scout (Sonnet 5.5, effort low), web search only.
---
Tonight a {{minutes}}-minute narrated audio chapter will be researched and written on this:

Topic: {{topic}}
Angle: {{angle}}

Before the plan is fixed, take a quick look at what exists. You have up to {{searches}} web searches and no page fetching. Suggested searches:

{{queries}}

Then write brief notes, under 300 words, for the person writing the plan:
- the strongest sources you saw (name each and what it seems to offer);
- whether there is enough solid material for {{minutes}} minutes on this angle;
- one or two things that surprised you and could sharpen the angle;
- anything suggesting the angle will not hold (a claim that looks contested or thin), and a better nearby angle if you saw one.

Only what the search results show. Do not invent sources or details.
