# ElevenLabs Agents: the three things to confirm before milestone 4

The Phase 1 doc asks to confirm these in ElevenLabs' current docs before building
voice. Checked 2026-11-12 from search results that quote the docs. The pages
themselves (elevenlabs.io) were not reachable from the cloud session, so
**reconfirm on the pages before building milestone 4.**

## 1. Client tools: yes, as the doc describes

Client tools run in the browser. The agent calls them by name, and a handler in
the page runs and can return a value to the agent. In the JavaScript SDK they are
passed as a `clientTools` object to `Conversation.startSession`:

```js
const conversation = await Conversation.startSession({
  agentId: "...",
  clientTools: {
    start_chapter: async ({ seed, why, notes }) => { /* POST to the local server */ return "started" },
  },
});
```

The tools themselves (name, description, parameters) are defined in the agent's
configuration on ElevenLabs' side; the handlers stay in the page. The SDK package
is reported as `@elevenlabs/client` (the older `@11labs/client` is deprecated):
check before installing. Source: [client tools](https://elevenlabs.io/docs/eleven-agents/customization/tools/client-tools.md).

## 2. Per-session prompt overrides: yes, but off by default; dynamic variables are the recommended way

- **Overrides** replace the agent's prompt, first message, language or voice for
  one conversation, passed as `overrides` when the session starts. Each field must
  first be enabled in the agent's Security settings, or the request fails. Newer
  pages also list the LLM, tools and knowledge base as overridable.
- **Dynamic variables** (recommended) fill `{{placeholders}}` in the agent's prompt,
  first message and tools at runtime: strings, numbers or booleans. A `secret__`
  prefix keeps a value out of the model's prompt.

For this project: keep the persona prompt in `prompts/guide.md` as the agent's
prompt, and pass the per-listener parts (profile, recent chapters, the guide's
identity, the name draw) as dynamic variables. Sources:
[overrides](https://elevenlabs.io/docs/eleven-agents/customization/personalization/overrides),
[dynamic variables](https://elevenlabs.io/docs/eleven-agents/customization/personalization/dynamic-variables).

## 3. Which Claude models the platform offers: Sonnet 5.5, not Haiku 5.5

The Agents LLM page lists Claude Opus 4.6, Sonnet 5.5, Sonnet 5, Sonnet 4.6,
Sonnet 4.5 and Haiku 4.5 as natively supported. Haiku 5.5 is not listed. The
live list comes from `GET /v1/convai/llm/list` (filtered by the account's data
residency), and a Custom LLM option (OpenAI Chat Completions format) can reach
any model through a gateway. Source: [LLM](https://elevenlabs.io/docs/eleven-agents/customization/llm).

**Decision:** the guide runs on Claude Sonnet 5.5 (`models.guide` in
config.yaml), already in milestone 3's typed chat, so the persona is tuned on the
model voice will use.
