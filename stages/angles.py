"""Stage 2: map -> 02_angles.md

Three or four candidate chapter angles for Ian to choose from.
"""

from . import llm
from .common import Run, load_prompt


def run(r: Run, cfg: dict):
    episode_map = r.read("map")
    r.check_writable("angles")
    prompt = load_prompt("angles", map=episode_map,
                         target_words=cfg["chapter"]["target_words"],
                         target_minutes=cfg["chapter"]["target_minutes"])
    res = llm.call(r, cfg, "angles", messages=[{"role": "user", "content": prompt}])
    out = r.write("angles", res.text)
    print(f"\n  Next: python cli.py choose {r.name} (the commissioning editor picks the angle).")
    return out
