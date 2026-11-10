"""The one place that talks to the Claude API.

Every call is streamed (long inputs and outputs), logged to the run's log.jsonl
with tokens and cost, and checked against the cost guard before it is sent.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from .common import ROOT, Run, StageError

_client = None


def get_client():
    """Build the Anthropic client once. The key comes from .env or the environment, never code."""
    global _client
    if _client is None:
        import anthropic
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise StageError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add it.")
        _client = anthropic.Anthropic(max_retries=4)
    return _client


# --- cost -------------------------------------------------------------------

def _prices(cfg: dict, model: str) -> dict:
    table = cfg["pricing"]["models"]
    if model in table:
        return table[model]
    # An unknown model (e.g. a server-side fallback we did not price): charge at
    # the most expensive known rate so the log never understates spend.
    return max(table.values(), key=lambda p: p["output"])


def token_cost(cfg: dict, model: str, *, input_tokens: int = 0, output_tokens: int = 0,
               cache_read: int = 0, cache_write_5m: int = 0, cache_write_1h: int = 0) -> float:
    p = _prices(cfg, model)
    per = 1_000_000
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read * p["input"] * cfg["pricing"]["cache_read_multiplier"]
        + cache_write_5m * p["input"] * cfg["pricing"]["cache_write_5m_multiplier"]
        + cache_write_1h * p["input"] * cfg["pricing"]["cache_write_1h_multiplier"]
    ) / per


def _cache_writes(usage) -> tuple[int, int]:
    """Split cache writes into (5m, 1h). Unknown split counts as 5m."""
    total = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cc = getattr(usage, "cache_creation", None)
    if cc is not None:
        w5 = getattr(cc, "ephemeral_5m_input_tokens", 0) or 0
        w1 = getattr(cc, "ephemeral_1h_input_tokens", 0) or 0
        if w5 + w1:
            return w5, w1
    return total, 0


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    web_searches: int = 0
    web_fetches: int = 0
    cost_usd: float = 0.0
    served_by: list[str] = field(default_factory=list)


def usage_and_cost(cfg: dict, requested_model: str, message) -> Usage:
    """Turn a response's usage into tokens and dollars.

    When the API reports per-iteration usage (server tool loops, fallbacks), each
    iteration is priced at the model that actually ran it.
    """
    u = message.usage
    out = Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
    )
    out.cache_write_5m, out.cache_write_1h = _cache_writes(u)
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        out.web_searches = getattr(stu, "web_search_requests", 0) or 0
        out.web_fetches = getattr(stu, "web_fetch_requests", 0) or 0

    iterations = getattr(u, "iterations", None) or []
    priced = [it for it in iterations if getattr(it, "type", None) in ("message", "fallback_message")]
    if priced:
        for it in priced:
            model = getattr(it, "model", None) or requested_model
            w5, w1 = _cache_writes(it)
            out.cost_usd += token_cost(
                cfg, model,
                input_tokens=it.input_tokens, output_tokens=it.output_tokens,
                cache_read=it.cache_read_input_tokens, cache_write_5m=w5, cache_write_1h=w1,
            )
            if model not in out.served_by:
                out.served_by.append(model)
    else:
        model = getattr(message, "model", None) or requested_model
        out.cost_usd = token_cost(
            cfg, model,
            input_tokens=out.input_tokens, output_tokens=out.output_tokens,
            cache_read=out.cache_read, cache_write_5m=out.cache_write_5m,
            cache_write_1h=out.cache_write_1h,
        )
        out.served_by.append(model)

    out.cost_usd += out.web_searches * cfg["pricing"]["web_search_per_1000"] / 1000
    return out


# --- cost guard -------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count for the cost guard. English prose runs ~4 chars/token;
    3.5 errs on the high side."""
    return int(len(text) / 3.5) + 1


def guard(run: Run, cfg: dict, what: str, estimate_usd: float) -> None:
    """Stop and ask before any call or stage expected to cost more than the limit."""
    limit = cfg["cost_guard_usd"]
    if estimate_usd <= limit:
        return
    msg = f"{what} is estimated at ${estimate_usd:.2f}, over the ${limit:.2f} guard."
    if run.yes:
        print(f"[cost guard] {msg} Proceeding (--yes).")
        return
    try:
        answer = input(f"[cost guard] {msg} Continue? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        raise StageError(f"Stopped by the cost guard: {msg} Re-run with --yes to approve.")


def estimate_call(cfg: dict, model: str, input_tokens: int, expected_output_tokens: int,
                  searches: int = 0) -> float:
    return (
        token_cost(cfg, model, input_tokens=input_tokens, output_tokens=expected_output_tokens)
        + searches * cfg["pricing"]["web_search_per_1000"] / 1000
    )


# --- the call ---------------------------------------------------------------

@dataclass
class Result:
    text: str
    message: object
    usage: Usage
    content: list = field(default_factory=list)  # every content block across continuations


def _text_of(content) -> str:
    return "".join(b.text for b in content if getattr(b, "type", None) == "text")


def _system_blocks(system: str | list | None, cache: bool):
    if system is None:
        return None
    if isinstance(system, list):
        return system
    block = {"type": "text", "text": system}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def call(
    run: Run,
    cfg: dict,
    stage: str,
    *,
    messages: list,
    system: str | list | None = None,
    role: str = "writer",
    tools: list | None = None,
    max_tokens: int | None = None,
    effort: str | None = None,
    cache_system: bool = True,
    label: str | None = None,
    expected_output_tokens: int | None = None,
    expected_searches: int = 0,
) -> Result:
    """Send one request (plus any pause_turn continuations) and log it.

    `role` picks the model from config: "writer" (Opus) or "cheap" (Haiku).
    Stage settings (max_tokens, effort) come from config unless overridden.
    """
    model = cfg["models"][role]
    st = cfg["stages"].get(stage, {})
    max_tokens = max_tokens or st.get("max_tokens", 16000)
    effort = effort or st.get("effort")

    prompt_tokens = estimate_tokens(str(system or "") + "".join(str(m["content"]) for m in messages))
    # Tool loops resend growing context, so a stage can declare a larger expected input.
    prompt_tokens = max(prompt_tokens, st.get("expected_input_tokens", 0))
    est = estimate_call(cfg, model, prompt_tokens,
                        expected_output_tokens or st.get("expected_output_tokens", max_tokens),
                        expected_searches)
    guard(run, cfg, f"{label or stage} ({model})", est)

    import anthropic
    import httpx2

    client = get_client()
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": list(messages),
    }
    sys_blocks = _system_blocks(system, cache_system)
    if sys_blocks:
        kwargs["system"] = sys_blocks
    if tools:
        kwargs["tools"] = tools
        # Server tool loops resend the growing context on every iteration and
        # continuation; automatic caching makes those re-reads cheap.
        kwargs["cache_control"] = {"type": "ephemeral"}
    if effort:
        kwargs["output_config"] = {"effort": effort}

    fallbacks = cfg.get("refusal_fallbacks") if role == "writer" else None
    if fallbacks:
        kwargs["fallbacks"] = fallbacks
        kwargs["betas"] = [cfg["refusal_fallbacks_beta"]]
        stream_fn = client.beta.messages.stream
    else:
        stream_fn = client.messages.stream

    total = Usage()
    all_content: list = []
    max_continuations = cfg.get("max_pause_continuations", 8)
    message = None
    overrun_approved = False
    request_id = None
    for attempt in range(max_continuations + 1):
        t0 = time.monotonic()
        try:
            with stream_fn(**kwargs) as stream:
                message = stream.get_final_message()
                request_id = getattr(stream, "request_id", None)
        except anthropic.APIStatusError as e:
            run.log({"stage": stage, "label": label or stage, "provider": "anthropic", "model": model,
                     "error": f"{e.status_code} {e.message}", "request_id": e.request_id, "cost_usd": 0})
            raise StageError(f"{label or stage}: Claude API error {e.status_code}: {e.message}") from None
        except (anthropic.APIConnectionError, httpx2.TransportError) as e:
            # A dropped stream mid-way: keep what earlier continuations produced.
            if all_content:
                (run.root / f"_partial_{label or stage}.md").write_text(_text_of(all_content), encoding="utf-8")
            run.log({"stage": stage, "label": label or stage, "provider": "anthropic", "model": model,
                     "error": f"connection: {e}", "cost_usd": 0})
            raise StageError(f"{label or stage}: lost the connection to the Claude API ({e}). "
                             f"Earlier continuations cost ${total.cost_usd:.2f}.") from None
        seconds = round(time.monotonic() - t0, 1)

        u = usage_and_cost(cfg, model, message)
        for f in ("input_tokens", "output_tokens", "cache_read", "cache_write_5m",
                  "cache_write_1h", "web_searches", "web_fetches", "cost_usd"):
            setattr(total, f, getattr(total, f) + getattr(u, f))
        for m in u.served_by:
            if m not in total.served_by:
                total.served_by.append(m)
        all_content.extend(message.content)

        run.log({
            "stage": stage,
            "label": label or stage,
            "provider": "anthropic",
            "model": model,
            "served_by": u.served_by,
            "request_id": request_id or getattr(message, "_request_id", None),
            "stop_reason": message.stop_reason,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read,
            "cache_write_tokens": u.cache_write_5m + u.cache_write_1h,
            "web_searches": u.web_searches,
            "web_fetches": u.web_fetches,
            "cost_usd": round(u.cost_usd, 5),
            "seconds": seconds,
            "continuation": attempt,
        })

        if message.stop_reason == "pause_turn":
            # Server-side tool loop paused: send the paused turn back and the API resumes.
            kwargs["messages"] = kwargs["messages"] + [
                {"role": "assistant", "content": message.content}
            ]
            # Tools stay byte-identical across continuations: editing them would
            # break the API's check on the earlier turn's thinking blocks. So
            # max_uses caps each request, and spend is watched here instead.
            next_input = u.input_tokens + u.cache_read + u.cache_write_5m + u.cache_write_1h + u.output_tokens
            projected = total.cost_usd + token_cost(cfg, model, input_tokens=next_input,
                                                    output_tokens=st.get("expected_output_tokens", 8000))
            if not overrun_approved and projected > cfg["cost_guard_usd"]:
                guard(run, cfg, f"{label or stage} after {attempt + 1} continuation(s), "
                      f"${total.cost_usd:.2f} spent so far,", projected)
                overrun_approved = True
            continue
        break
    else:
        raise StageError(f"{stage}: still paused after {max_continuations} continuations.")

    text = _text_of(all_content)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise StageError(f"{stage}: the model declined ({getattr(details, 'category', None)}). "
                         f"{getattr(details, 'explanation', '') or ''}")
    if message.stop_reason == "max_tokens":
        partial = run.root / f"_partial_{label or stage}.md"
        partial.write_text(text, encoding="utf-8")
        raise StageError(f"{stage}: hit max_tokens ({max_tokens}); partial output saved to "
                         f"{partial.name}. Raise stages.{stage}.max_tokens in config.yaml.")
    if not text.strip():
        raise StageError(f"{stage}: the model returned no text (stop_reason={message.stop_reason}).")

    print(f"  {label or stage}: {total.input_tokens + total.cache_read + total.cache_write_5m + total.cache_write_1h:,} in "
          f"({total.cache_read:,} cached), {total.output_tokens:,} out, "
          f"{total.web_searches} searches, ${total.cost_usd:.3f}")
    return Result(text=text.strip(), message=message, usage=total, content=all_content)
