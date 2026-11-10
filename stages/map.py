"""Stage 1: transcript -> 01_map.md

Every thinker, work, claim and example in the episode, with what each is used
to argue, and the threads the episode only skims.
"""

from . import llm
from .common import Run, load_prompt


def run(r: Run, cfg: dict):
    transcript = r.read("transcript")
    r.check_writable("map")
    prompt = load_prompt("map", transcript=transcript)
    res = llm.call(r, cfg, "map", messages=[{"role": "user", "content": prompt}])
    return r.write("map", res.text)
