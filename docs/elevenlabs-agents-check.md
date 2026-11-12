# ElevenLabs Agents: the three things to confirm before milestone 4

The Phase 1 doc asks to confirm these in ElevenLabs' current docs before building
voice. Checked 2026-11-12. The docs site (elevenlabs.io) was not reachable from the
cloud session, so points 1 and 2 were confirmed from the JavaScript SDK's own type
definitions (`@elevenlabs/client` 1.36.0 from npm, published 2026-11-10), and point 3
from search results quoting the docs. Ian confirmed point 3 on ElevenLabs' docs and
changelog the same day (below).

## Confirmed from the SDK types (`@elevenlabs/client` 1.36.0)

- `Conversation.startSession(options)`, where options include
  `clientTools: Record<string, (parameters) => Promise<string | number | void>>`:
  a handler can return a value to the agent.
- `overrides: { agent: { prompt, firstMessage, language }, tts: { voiceId, modelId,
  speed, stability, similarityBoost }, asr: { keywords }, conversation: { textOnly } }`
  and `dynamicVariables: Record<string, string | number | boolean>`.
- What milestone 4 also needs is there: `onModeChange` (speaking or listening, for
  the circle's states), `getInputVolume()` and `getOutputByteFrequencyData()` (for
  the circle to respond to the mic and the guide's voice), `onMessage` (for the
  transcript), `sendContextualUpdate(text)` (to tell the guide the chapter is
  ready without a user turn), `sendUserMessage(text)` (the text box), and a
  `textOnly` mode.

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

## 3. Which Claude models the platform offers: Sonnet 5.5 (confirmed by Ian, 2026-11-12)

ElevenLabs' Agents docs list Claude Sonnet 5.5 among the natively supported
Anthropic models, configured in the agent's settings, alongside Opus 5.5, Opus 5,
Sonnet 5 and Haiku 4.5. Haiku 5.5 is not listed. The changelog gives the API id
`claude-sonnet-5-5`, and says agents on Sonnet 5.5 can set reasoning effort to
`none`. A Custom LLM option (OpenAI Chat Completions format) can reach any other
model through a gateway. Source: [LLM](https://elevenlabs.io/docs/eleven-agents/customization/llm).

**Decision:** the guide runs on Claude Sonnet 5.5 (`models.guide` in
config.yaml), already in milestone 3's typed chat, so the persona is tuned on the
model voice will use. The typed chat runs at effort `low` (the API's lowest
setting here); for voice, try `none` first for latency and compare the guide's
replies against the typed chat's.

## Confirmed live, building milestone 4 (2026-11-12, local, Ian's account)

- `conversational_ai.llm.list()`: `claude-sonnet-5-5` offers reasoning effort
  `none`, `low`, `medium`, `high`, `xhigh`, `max`. `claude-haiku-5-5` is listed too
  (same efforts), although the docs page leaves it out: a lower-latency option if
  needed.
- Agent from code (`stages/voice.py`): `agents.create/update` with
  `conversation_config.agent.prompt.{prompt, llm, reasoning_effort, tool_ids}`,
  `tts.{model_id: eleven_v4_turbo, voice_id}`, and dynamic variable placeholders.
  The library voice `RILOU7YmBhvwJGDGjNmP` is accepted by id. The platform sets
  `temperature: 0.0` itself; conversations on Sonnet 5.5 work regardless.
- Client tools as separate tool objects (`tools.create` with a `client` tool
  config, then `tool_ids` on the agent): `parameters` is ElevenLabs' schema subset
  (no `additionalProperties`); a no-argument tool takes empty `properties`. The
  `start_chapter` handler receives `{topic, listener_said, why, notes}` (the
  example above predates the tool's final parameters).
- Private agent + signed URL (`conversations.get_signed_url`) works from the page
  with `@elevenlabs/client` 1.32.1 (IIFE build), and text-only sessions work over
  it once `platform_settings.overrides.conversation_config_override.conversation.text_only`
  is allowed. A client tool round trip (agent -> page -> local server -> agent)
  works.
- The call record (`conversations.get`) carries per-turn metrics
  `convai_llm_service_ttfb`, `convai_llm_service_ttf_sentence`,
  `convai_llm_service_tt_last_sentence`, `convai_ttf_response_since_user_message`
  and `convai_llm_tool_request_generation_latency`, and the cost in credits and
  dollars. A 6-message text-only conversation cost $0.06.
- Deleting an agent leaves its tools "in use" by the deleted agent's branch:
  delete them with `force=True`.
