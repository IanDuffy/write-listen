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
    print("\n  Next [Ian]: read 02_angles.md, then write 03_angle.md by hand: the angle you want,\n"
          "  edited as you like, plus any notes on what you want from the chapter.")
    return out
