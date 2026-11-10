"""Baseline: transcript + angle -> baseline/script.md

One Opus call, web search allowed, asked for the whole narration script.
No map, dossier, brief or edit. This is the best a single prompt can do, and
the pipeline chapter has to beat it in a blind listen.
"""

from . import llm
from .common import PROMPTS_DIR, Run, StageError, load_prompt, word_count
from .research import tools as research_tools


def run(r: Run, cfg: dict):
    transcript = r.read("transcript")
    angle = r.read("angle")
    r.check_writable("baseline_script")
    ch = cfg["chapter"]
    prompt = load_prompt("baseline", transcript=transcript, angle=angle,
                         style_guide=(PROMPTS_DIR / "style_guide.md").read_text(encoding="utf-8"),
                         target_words=ch["target_words"], target_minutes=ch["target_minutes"])
    res = llm.call(r, cfg, "baseline", messages=[{"role": "user", "content": prompt}],
                   tools=research_tools(cfg), expected_searches=cfg["baseline"]["max_searches"])
    # The model narrates between searches; keep only the last complete <script> block.
    end = res.text.rfind("</script>")
    start = res.text.rfind("<script>", 0, end) if end != -1 else -1
    if start == -1:
        partial = r.path("baseline_dir") / "_partial_baseline.md"
        partial.parent.mkdir(exist_ok=True)
        partial.write_text(res.text, encoding="utf-8")
        raise StageError("baseline: no complete <script> block in the response; raw output saved to "
                         "baseline/_partial_baseline.md. Nothing was written to baseline/script.md.")
    text = res.text[start + len("<script>"):end]
    out = r.write("baseline_script", text.strip())
    print(f"  baseline script: {word_count(text):,} words")
    return out
