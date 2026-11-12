"""Stage 2: map -> 02_angles.md

Three or four candidate chapter angles; the choose stage picks one. A seed run
(00_source/seed.md) uses prompts/angles_seed.md, with the seed and the listener's profile.
"""

from . import llm
from .common import Run, load_prompt, prompt_name, seed_inputs


def run(r: Run, cfg: dict):
    episode_map = r.read("map")
    r.check_writable("angles")
    prompt = load_prompt(prompt_name(r, "angles"), map=episode_map, **seed_inputs(r, cfg),
                         target_words=cfg["chapter"]["target_words"],
                         target_minutes=cfg["chapter"]["target_minutes"])
    res = llm.call(r, cfg, "angles", messages=[{"role": "user", "content": prompt}])
    out = r.write("angles", res.text)
    print(f"\n  Next: python cli.py choose {r.name} (the commissioning editor picks the angle).")
    return out
