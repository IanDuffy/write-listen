"""Milestone 1: one tiny call to each API, to prove the keys and logging work."""

from . import llm
from .common import Run, StageError
from .tts import elevenlabs_client


def run(r: Run, cfg: dict, anthropic: bool = True, elevenlabs: bool = True):
    ok = True
    if anthropic:
        try:
            res = llm.call(r, cfg, "ping", role="cheap", max_tokens=64,
                           messages=[{"role": "user", "content": "Reply with the single word: ready"}])
            print(f"  Claude ({cfg['models']['cheap']}): {res.text!r}. Logged to {r.path('log').relative_to(r.root.parent.parent)}")
        except StageError as e:
            ok = False
            print(f"  Claude: {e}")
    if elevenlabs:
        try:
            sub = elevenlabs_client().user.subscription.get()
            used, limit = sub.character_count, sub.character_limit
            print(f"  ElevenLabs: plan {sub.tier!r}, {used:,} of {limit:,} characters used this period.")
            r.log({"stage": "ping", "provider": "elevenlabs", "tier": sub.tier,
                   "characters_used": used, "character_limit": limit, "cost_usd": 0})
        except StageError as e:
            ok = False
            print(f"  ElevenLabs: {e}")
    if not ok:
        raise StageError("Ping failed; see above.")
