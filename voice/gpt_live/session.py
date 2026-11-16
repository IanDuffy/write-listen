"""One GPT-Live session for the guide: its instructions, tools and configuration, and opening it.

Spec: "One Live Voice (GPT-Live replaces ElevenLabs)" (Ian, 2026-11-13); results in voice/gpt_live/REPORT.md.
The guide's conversation and the chapter's reading run in one GPT-Live session, in one voice:

- `instructions(values)`: the parts in voice/gpt_live/instructions/ (files starting with a digit, in
  order), each with notes above a `---` line that are dropped, and {{placeholders}} filled from the
  conversation (guide.Session.values). Ian edits them without touching code.
- The voice model (gpt-live-1) speaks every word; the guide's tools run in a Responses backend
  (Terra) through GPT-Live's delegation: `tools()` turns prompts/guide_tools.json and the aside's
  tools into Responses function tools, and this app runs them (server.py /api/live/tool).
- `create_webrtc`: the page's SDP offer goes to OpenAI with the session configuration and the key
  stays here (GPT-Live has no ephemeral client keys); the answer goes back to the page.
- `create_websocket_session`: the same session from Python over a WebSocket, for the live checks
  (voice/gpt_live/sim.py).

Facts checked against OpenAI's docs and live (REPORT.md, "Doc questions"): there is no "opening"
session field (unknown_parameter for every name and place tried, 2026-11-14; the Playground's Opening
box is the Playground's own), so the page makes the guide speak first with speakable context the moment
the session starts (`greet=False` here; the WebSocket harness keeps the greeting in the startup input);
instructions are limited to 16,384 tokens and immutable; each appended context to 500 tokens.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from stages.common import PROMPTS_DIR, ROOT, StageError

HERE = Path(__file__).parent
INSTRUCTIONS_DIR = HERE / "instructions"
NOTES = HERE / "notes.md"
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
# The guide's tools: the onboarding ones (prompts/guide_tools.json), since the reading is in the
# same session the aside's (prompts/guide_aside_tools.json: resume_chapter, save_seed), and the live
# session's own (prompts/live_tools.json: start_reading, a ready chapter begun on the listener's yes).
TOOL_FILES = ("guide_tools.json", "guide_aside_tools.json", "live_tools.json")
FAST_ONLY = ("scout_topic",)
# Left out of the live guide (Ian's second test, 2026-11-15): in two of three persona conversations the voice
# spawned scout_topic at the moment of the pitch and then stalled ("Hmm", "Let me think a sec") for minutes
# waiting on a result that brings nothing back. The research starts with start_chapter instead.
LIVE_LEAVE_OUT = ("scout_topic",)
PAGE_TOOLS = ("step_back", "resume_chapter", "start_reading")  # the page acts on these too (mute, the feed loop)
APPEND_TOKENS = 500  # GPT-Live: each appended context (commentary, thinking, instructions) at most
INSTRUCTION_TOKENS = 16384


def _body(text: str) -> str:
    parts = re.split(r"^---[ \t]*$", text, maxsplit=1, flags=re.M)
    return (parts[1] if len(parts) == 2 else parts[0]).strip()


def part_files(folder: Path = INSTRUCTIONS_DIR) -> list[Path]:
    """The guide's instruction parts, in order: files whose name starts with a digit."""
    return sorted(p for p in folder.glob("*.md") if p.name[:1].isdigit())


def fill(text: str, values: dict, where: str) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise StageError(f"{where} uses {{{{{key}}}}} but no value was given.")
        return str(values[key])
    return PLACEHOLDER.sub(sub, text)


def placeholders(folder: Path = INSTRUCTIONS_DIR) -> set[str]:
    return {m.group(1) for p in part_files(folder) for m in PLACEHOLDER.finditer(_body(p.read_text(encoding="utf-8")))}


def instructions(values: dict, folder: Path = INSTRUCTIONS_DIR) -> str:
    """The voice model's instructions: every part, filled, separated by a blank line."""
    parts = [fill(_body(p.read_text(encoding="utf-8")), values, f"voice/gpt_live/instructions/{p.name}")
             for p in part_files(folder)]
    text = "\n\n".join(parts) + "\n"
    if approx_tokens(text) > INSTRUCTION_TOKENS:
        raise StageError(f"The guide's instructions are about {approx_tokens(text):,} tokens; GPT-Live takes "
                         f"{INSTRUCTION_TOKENS:,}.")
    return text


def backend_instructions(folder: Path = INSTRUCTIONS_DIR) -> str:
    return _body((folder / "backend.md").read_text(encoding="utf-8")) + "\n"


def notes() -> dict[str, str]:
    """The page's bracketed notes (voice/gpt_live/notes.md): `key: text` lines below the `---`."""
    out = {}
    for line in _body(NOTES.read_text(encoding="utf-8")).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def approx_tokens(text: str) -> int:
    """A conservative token estimate (English runs about four characters a token; this uses 3.5)."""
    return int(len(text) / 3.5) + 1


def pronunciations(cfg: dict) -> str:
    table = (cfg.get("narration") or {}).get("pronunciations") or {}
    return "\n".join(f"- {word}: say it like \"{said}\"" for word, said in table.items()) or "- (none)"


# Names the guide used before 20_meeting.md took the conversation's drawn names ({{names}}, from
# prompts/guide_names.md); still recognised in a transcript.
LIVE_NAMES = ("Wren", "Ilsa", "Tobin", "Maren", "Oriel", "Juno", "Calder", "Sable")


def name_said(transcript: Path, names=None) -> str | None:
    """The name the guide gave itself in this conversation (the first of `names`, the conversation's
    drawn names, it said; else of LIVE_NAMES), for a new session that takes the conversation over."""
    if not transcript.exists():
        return None
    spoken = " ".join(json.loads(x).get("text", "") for x in transcript.read_text(encoding="utf-8").splitlines()
                      if '"who": "guide"' in x)
    found = [(m.start(), n) for n in (names or LIVE_NAMES) if (m := re.search(rf"\b{re.escape(n)}\b", spoken))]
    return min(found)[1] if found else None


def meeting(values: dict) -> str:
    first = str(values.get("identity", "")).startswith("(none")
    return ("You are meeting this listener for the first time." if first
            else "You have met this listener before.")


def values_for(cfg: dict, session_values: dict) -> dict:
    """The instruction parts' values: the conversation's (guide.Session.values or resume_values),
    plus what only the live session needs."""
    v = dict(session_values)
    v["meeting"] = meeting(v)
    v["pronunciations"] = pronunciations(cfg)
    # What this is: offered once. A returning listener, or one told already in this conversation (a resumed
    # session's "explained"), is never offered it again (Ian's fourth test).
    known = not str(v.get("identity", "")).startswith("(none") or bool(v.get("explained"))
    name = "offer_known.md" if known else "offer_first.md"
    v["offer"] = fill(_body((INSTRUCTIONS_DIR / name).read_text(encoding="utf-8")), v, f"voice/gpt_live/instructions/{name}")
    return v


EXPLAINED = re.compile(r"\baudio chapters?\b", re.I)


def explained(transcript: Path) -> bool:
    """Whether the guide has told the listener what it does in this conversation (its own lines)."""
    if not transcript.exists():
        return False
    return any(EXPLAINED.search(json.loads(x).get("text", "")) for x in transcript.read_text(encoding="utf-8").splitlines()
               if '"who": "guide"' in x)


# --- tools --------------------------------------------------------------------------------------

def _all_tools() -> list[dict]:
    out = []
    for name in TOOL_FILES:
        out += json.loads((PROMPTS_DIR / name).read_text(encoding="utf-8"))
    return out


def tool_names(fast: bool = True) -> list[str]:
    return [t["name"] for t in _all_tools() if fast or t["name"] not in FAST_ONLY]


# In the live session the guide leads back in its own words before the backend restarts the reading;
# the aside's tool text (written for the ElevenLabs agent) said the opposite.
LIVE_DESCRIPTIONS = {
    "resume_chapter": ("Restart the chapter's reading after a question or a check-in. The guide has already said "
                       "its few words of lead-back; reply with an empty message. Leave rewind out unless the "
                       "listener was lost (then 'paragraph')."),
}


def tools(fast: bool = True) -> list[dict]:
    """The guide's tools as Responses function tools for the backend."""
    return [{"type": "function", "name": t["name"], "description": LIVE_DESCRIPTIONS.get(t["name"], t["description"]),
             "parameters": t["input_schema"], "strict": False}
            for t in _all_tools() if (fast or t["name"] not in FAST_ONLY) and t["name"] not in LIVE_LEAVE_OUT]


# --- the session --------------------------------------------------------------------------------

def live_cfg(cfg: dict) -> dict:
    return cfg["live"]


def greeting(cfg: dict) -> str:
    return notes()["opened"]


def session_config(cfg: dict, values: dict, *, transport: str = "webrtc", fast: bool = True,
                   earlier: list[dict] | None = None, opening: str | None = None, greet: bool = True) -> dict:
    """The GPT-Live session: gpt-live-1 speaking in Gleam with the guide's instructions, the greeting
    as a developer message in its startup input, and the tools in a Terra backend. `opening`: another
    first message instead of the greeting (a session that takes over a conversation already going).
    `greet=False` (the page, which greets with speakable context once the session starts): no startup
    message unless `opening` is given, so the guide is never asked to greet twice.
    `transport` "websocket" adds the audio format (WebRTC negotiates its own)."""
    L = live_cfg(cfg)
    v = values_for(cfg, values)
    text = opening or (greeting(cfg) if greet else None)
    first = [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]}] if text else []
    session = {
        "model": L["model"],
        "instructions": instructions(v),
        "audio": {"output": {"voice": L["voice"]}},
        "input": (earlier or []) + first,
        "delegation": {
            "type": "responses",
            "responses": {
                "model": L["backend_model"],
                "instructions": backend_instructions(),
                "tools": tools(fast),
                "tool_choice": "auto",
                "reasoning": {"effort": L.get("backend_effort", "low")},
            },
        },
        "store": bool(L.get("store", False)),
    }
    if transport == "websocket":
        session["audio"]["format"] = {"type": "audio/pcm", "rate": 24000}
    return session


def api_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise StageError("No OpenAI key: set OPENAI_API_KEY in .env (see README, 'API keys').")
    return key


def create_webrtc(cfg: dict, session: dict, offer_sdp: str, client=None) -> dict:
    """Open the session for the page: {"id": the GPT-Live session id, "sdp": the answer}."""
    import httpx

    http = client or httpx
    res = http.post(live_cfg(cfg)["url"], headers={"Authorization": f"Bearer {api_key()}"},
                    json={"session": session, "transport": {"type": "webrtc", "sdp": offer_sdp}}, timeout=20)
    if res.status_code >= 400:
        try:
            err = res.json().get("error", {})
            msg = err.get("message") or res.text
        except ValueError:
            msg = res.text
        raise StageError(f"GPT-Live refused the session ({res.status_code}): {msg[:300]}")
    body = res.json()
    return {"id": body["session"]["id"], "sdp": body["transport"]["sdp"]}


def recording(cfg: dict, live_id: str, out: Path, client=None) -> Path | None:
    """Download a stored session's recording (stereo WAV: the listener left, the guide right), or None
    when there is none (store off, or not ready yet). OpenAI keeps it for 30 days."""
    import httpx

    http = client or httpx
    res = http.get(f"{live_cfg(cfg)['url']}/{live_id}/content", headers={"Authorization": f"Bearer {api_key()}"},
                   timeout=120, follow_redirects=True)
    if res.status_code != 200 or not res.content:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(res.content)
    return out


def cost(cfg: dict, seconds: float, backend: dict | None = None) -> float:
    """Dollars for a session: its connected seconds at the per-minute rate, plus backend tokens
    ({"model", "input", "output", "cached"})."""
    L = live_cfg(cfg)
    usd = seconds / 60 * L["price_per_minute"]
    if backend:
        rates = (L.get("backend_prices") or {}).get(backend.get("model") or L["backend_model"])
        if rates:
            i, o, c = rates
            cached = backend.get("cached", 0) or 0
            usd += ((backend.get("input", 0) - cached) * i + cached * c + backend.get("output", 0) * o) / 1e6
    return round(usd, 6)


def split_for_append(text: str, limit: int = APPEND_TOKENS) -> list[str]:
    """Text cut into appends of at most `limit` tokens (estimated), at paragraph, then sentence,
    then word boundaries."""
    if approx_tokens(text) <= limit:
        return [text]
    out, cur = [], ""
    pieces = re.split(r"(?<=[.!?])\s+|\n\s*\n", text)
    for piece in pieces:
        while approx_tokens(piece) > limit:  # a very long sentence: cut at a word
            words, head = piece.split(), []
            while words and approx_tokens(" ".join(head + [words[0]])) <= limit:
                head.append(words.pop(0))
            if not head:
                head = [words.pop(0)]
            out.append(" ".join(head))
            piece = " ".join(words)
        if cur and approx_tokens(cur + " " + piece) > limit:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        out.append(cur)
    return out
