"""Baseline: transcript + angle -> baseline/script.md

One Opus call, web search allowed, asked for the whole narration script.
No map, dossier, brief or edit. This is the best a single prompt can do, and
the pipeline chapter has to beat it in a blind listen.
"""

from . import llm
from .common import Run, load_prompt, word_count
from .research import tools as research_tools


def run(r: Run, cfg: dict):
    transcript = r.read("transcript")
    angle = r.read("angle")
    r.check_writable("baseline_script")
    ch = cfg["chapter"]
    prompt = load_prompt("baseline", transcript=transcript, angle=angle,
                         target_words=ch["target_words"], target_minutes=ch["target_minutes"])
    res = llm.call(r, cfg, "baseline", messages=[{"role": "user", "content": prompt}],
                   tools=research_tools(cfg), expected_searches=cfg["research"]["max_searches"])
    text = res.text
    start = text.find("<script>")
    end = text.rfind("</script>")
    if start != -1 and end > start:
        text = text[start + len("<script>"):end]
    out = r.write("baseline_script", text.strip())
    print(f"  baseline script: {word_count(text):,} words")
    return out
