"""Stage 3: map + angles -> 03_angle.md

The commissioning editor: one Opus call picks (and may tighten) one angle. This
replaces the [Ian] checkpoint where Ian wrote 03_angle.md by hand, because the
pipeline runs hands-off. A hand-written 03_angle.md is never overwritten without --force.
"""

import re

from . import llm
from .common import Run, StageError, load_prompt, prompt_name, seed_inputs


def run(r: Run, cfg: dict):
    episode_map, angles = r.read("map"), r.read("angles")
    r.check_writable("angle")
    prompt = load_prompt(prompt_name(r, "choose"), map=episode_map, angles=angles, **seed_inputs(r, cfg),
                         target_words=cfg["chapter"]["target_words"],
                         target_minutes=cfg["chapter"]["target_minutes"])
    res = llm.call(r, cfg, "choose", messages=[{"role": "user", "content": prompt}])
    text = res.text.strip()
    title = re.search(r"^# Angle:\s*(.+)$", text, re.M)
    if not title:
        raise StageError("choose: the reply has no '# Angle: <title>' line; nothing written. "
                         "Rerun, or write 03_angle.md by hand.")
    out = r.write("angle", text + "\n")
    r.log({"stage": "choose", "provider": "none", "decision": title.group(1).strip(), "cost_usd": 0})
    print(f"  chose: {title.group(1).strip()}")
    why = re.search(r"^## Why this one\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    r.decide("angle", f"Chose \"{title.group(1).strip()}\" from 02_angles.md."
             + (f"\n\n{why.group(1).strip()}" if why else ""))
    return out
