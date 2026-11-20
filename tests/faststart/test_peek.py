"""The guide's peek (faststart/peek.py): a few concrete facts about a fast chapter being made, by code
from the files it has written, for get_status. Offline: tiny run folders, no model."""

import json
from pathlib import Path

from faststart import peek as P
from stages.common import ROOT

CLOSING = "the stillness turns out to be learned after all"
HAND_OFF = "Ends on the gray heron frozen mid-stance with a secret"
SEGMENT = "Picture a heron at dawn on a grey river bank, waiting."

OUTLINE = {
    "title": "What the Heron Is Doing", "angle": "A heron's stillness is the hunt. More follows.",
    "through_line": "What is a heron actually doing while it stands so still?",
    "closing_idea": CLOSING,
    "sections": [
        {"n": 1, "title": "The Twig", "lands": "Some herons do not just wait for fish; they set a lure on the "
                                               "water and wait at a trap they made, which suggests more work.",
         "hand_off": HAND_OFF, "sets_up": "x", "notes": "secret notes"},
        {"n": 2, "title": "Water That Lies", "lands": "This section settles how light bends at the surface.",
         "hand_off": HAND_OFF},
        {"n": 3, "title": "The Gray Heron", "lands": "This section settles whether the gray heron is the usual "
                                                     "heron across the places it lives, and what studies of gray "
                                                     "herons show about their standing and their strike.",
         "hand_off": HAND_OFF},
    ]}

LEDGER = {"rows": [
    {"id": "L01", "section": 2, "idea": "The reef heron's strike has two phases, and the correction for refraction "
                                        "seems to happen where one hands over to the other; to settle: how the study "
                                        "measured this."},
    {"id": "L02", "section": 2, "idea": "Herons differ in how well they correct for refraction; to settle: what "
                                        "Katzir found comparing cattle egrets with little egrets."},
    {"id": "L03", "section": 3, "idea": "To settle: how widespread and common the gray heron, Ardea cinerea, is across "
                                        "its range, which other herons share that range, and how it hunts."},
    {"id": "L04", "section": 3, "idea": "Herons can strike fast; to settle: why."},
]}

HEAD = "# Dossier\n\nIntro.\n\n## Entries\n\n"
BLOCKS = {
    "L00": "### L00: what the scout found\n\n"
           "[D01] (L00, claim, tier 3) It has been reported in green herons and many other birds across the world. "
           "Source: x\n\n"
           "[D02] (L00, material, tier 3) Higuchi recorded many striated herons bait-fishing at one site in Japan. "
           "More. Source: x\n\n",
    "L02": "### L02: refraction\n\n"
           "[D03] (L02, quote, tier 1, verbatim: yes) \"A quote that is long enough to count here.\" Source: x\n\n"
           "[D04] (L02, claim, tier 1) Cattle egrets corrected for refraction less well than little egrets in tanks. "
           "Source: x\n\n",
    "L01": "### L01: two phases\n\n"
           "[D05] (L01, claim, tier 2) The same study also timed the strike in two distinct phases of the head. "
           "Source: x\n\n"
           "[D06] (L01, material, tier 1) Reef herons struck in two phases, adjusting the aim between the two. "
           "Source: x\n\n",
    "L03": "### L03: gray heron\n\n"
           "[D07] (L03, claim, tier 1) The Gray Heron is a widespread Old World species across Europe, Asia and Africa. "
           "Source: x\n\n",
}
TAIL = "## Not found\n\n- nothing\n"


def making(**more):
    return {"state": "making", "mode": "fast", "first_audio": False, "sections": [], **more}


def sections(ready=()):
    return [{"n": s["n"], "title": s["title"], "ready": s["n"] in ready} for s in OUTLINE["sections"]]


def world(tmp_path, *, events=(), outline=True, card=True, rows=None, state=None, blocks=("L00",)):
    root = tmp_path / "run"
    (root / "fast").mkdir(parents=True)
    (root / "06_segments").mkdir()
    (root / "06_segments" / "seg01.md").write_text(SEGMENT)
    if card:
        (root / "fast" / "card.json").write_text(json.dumps({"overview": "A heron's stillness is the hard part of "
                                                                         "the hunt. Then more."}))
    tl = [{"t": 0, "event": "seed_set"}, {"t": 1, "event": "scout_done"}] + [dict(e) for e in events]
    (root / "fast_timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in tl))
    if outline:
        (root / "05_outline.json").write_text(json.dumps(outline if isinstance(outline, dict) else OUTLINE))
        (root / "ledger.json").write_text(json.dumps(rows or LEDGER))
    if state is not None:
        (root / "fast" / "state.json").write_text(json.dumps({"rows": state}))
    (root / "04_dossier.md").write_text(HEAD + "".join(BLOCKS[b] for b in blocks) + TAIL)
    return root


def start(row):
    return {"event": "research_start", "row": row}


def done(row):
    return {"event": "research_done", "row": row}


def total(p):
    return sum(P.words(v) if isinstance(v, str) else sum(map(P.words, v)) for v in p.values())


def test_no_peek_unless_making_fast(tmp_path):
    root = world(tmp_path)
    assert P.peek(root, making(state="ready")) == {}
    assert P.peek(root, making(state="stopped")) == {}
    assert P.peek(root, making(state="problem")) == {}
    assert P.peek(root, making(queued=True)) == {}
    assert P.peek(root, {**making(), "mode": None}) == {}
    assert P.peek(tmp_path / "missing", making()) == {}
    assert P.peek(root, None) == {}


def test_before_the_outline_card_and_scout(tmp_path):
    root = world(tmp_path, outline=False)
    p = P.peek(root, making())
    # about: the card's first sentence; found: L00 at tier 3 (D01 leans on "it": skipped)
    assert p == {"about": "A heron's stillness is the hard part of the hunt.",
                 "found": "Higuchi recorded many striated herons bait-fishing at one site in Japan."}
    # once the outline exists the card gives way to the question
    root2 = world(tmp_path / "b")
    assert "about" not in P.peek(root2, making(sections=sections()))
    assert P.peek(root2, making(sections=sections()))["question"] == OUTLINE["through_line"]


def test_writing_the_opening(tmp_path):
    root = world(tmp_path, events=[start("L01"), start("L02"), start("L03")])
    p = P.peek(root, making(sections=sections()))
    assert p["working_on"] == 'part 1, "The Twig": some herons do not just wait for fish'
    # L01: 5 words after "to settle", 22 before it (too long): skipped. L02: after. L03: after, but too long.
    assert p["looking_into"] == ["what Katzir found comparing cattle egrets with little egrets"]
    assert p["found"] == "Higuchi recorded many striated herons bait-fishing at one site in Japan."
    # "to settle" with too little either side: the part before it, only when that reads as a question
    assert P.question_of({"idea": "How herons strike fast with a long neck; to settle: why."}) == \
        "How herons strike fast with a long neck"
    assert P.question_of({"idea": "Herons can strike fast with a long neck; to settle: why."}) is None  # a claim
    assert P.question_of(LEDGER["rows"][3]) is None


def test_running_comes_from_the_timeline_not_stale_state(tmp_path):
    state = {"L01": {"section": 2, "state": "running"}, "L02": {"section": 2, "state": "running"},
             "L03": {"section": 3, "state": "running"}}
    rows = {"rows": [*LEDGER["rows"][:2], {**LEDGER["rows"][2], "idea": "To settle: which herons share the gray "
                                                                         "heron's range across Europe and Asia."}]}
    four = {**OUTLINE, "sections": [*OUTLINE["sections"], {"n": 4, "title": "A Fourth Part", "lands": "x"}]}
    root = world(tmp_path, events=[start("L02"), start("L03"), done("L03")], rows=rows, state=state,
                 blocks=("L00", "L03"), outline=four)  # section 3 is not the last here, so its finding may be said
    p = P.peek(root, making(sections=sections()))
    assert p["looking_into"] == ["what Katzir found comparing cattle egrets with little egrets"]
    assert not any("Europe" in q for q in p["looking_into"])
    assert p["found"] == "The Gray Heron is a widespread Old World species across Europe, Asia and Africa."


def test_after_first_audio_next_unready_part_and_its_findings(tmp_path):
    events = [start("L01"), start("L02"), start("L03"), done("L02"), done("L03"), done("L01")]
    root = world(tmp_path, events=events, blocks=("L00", "L02", "L03", "L01"))
    p = P.peek(root, making(first_audio=True, sections=sections(ready=(1,))))
    assert p["working_on"] == 'part 2, "Water That Lies": how light bends at the surface'
    # part 2's rows, newest first: L01 (D05 leans on "same"/"also", D06 stands alone)
    assert p["found"] == "Reef herons struck in two phases, adjusting the aim between the two."
    assert "looking_into" not in p
    # part 3 next, the last: its title alone (its lands is the ending), and a finding of its own as the fresh tease
    p3 = P.peek(root, making(first_audio=True, sections=sections(ready=(1, 2))))
    assert p3["working_on"] == 'part 3, "The Gray Heron"'
    assert p3["found"] == "The Gray Heron is a widespread Old World species across Europe, Asia and Africa."
    # every part ready: nothing being worked on
    assert "working_on" not in P.peek(root, making(first_audio=True, sections=sections(ready=(1, 2, 3))))


def test_pending_rows_of_the_part_when_none_run(tmp_path):
    state = {"L01": {"state": "pending"}, "L02": {"state": "pending"}, "L03": {"state": "pending"}}
    root = world(tmp_path, state=state)
    p = P.peek(root, making(first_audio=True, sections=sections(ready=(1,))))
    assert p["looking_into"] == ["what Katzir found comparing cattle egrets with little egrets"]


def test_limits_and_never_spoils(tmp_path):
    long_rows = {"rows": [{"id": f"L0{i}", "section": 1, "idea": "to settle: " + " ".join(["word"] * (6 + i))}
                          for i in range(1, 6)]}
    root = world(tmp_path, events=[start(f"L0{i}") for i in range(1, 6)], rows=long_rows)
    p = P.peek(root, making(sections=sections()))
    assert len(p.get("looking_into", [])) <= 2
    assert total(p) <= P.WORDS_ALL
    for key, v in p.items():
        for text in (v if isinstance(v, list) else [v]):
            cap = P.WORDS_LONG if key in ("question", "about") else P.WORDS_EACH + 6  # working_on adds the title
            assert P.words(text) <= cap
    dumped = json.dumps(p)
    for secret in (CLOSING, HAND_OFF, SEGMENT, "secret notes"):
        assert secret not in dumped
    strings = [t for v in p.values() for t in (v if isinstance(v, list) else [v])]
    assert not any("[" in t or "Source:" in t for t in strings)
    assert P.lands({"lands": "This section settles how light bends."}) == "how light bends"
    assert P.lands({"lands": " ".join(["long"] * 25) + "."}) is None
    assert P.lands({"lands": "Higuchi's herons bait-fished; " + " ".join(["more"] * 25)}) == "Higuchi's herons bait-fished"
    # a full peek stays within the whole cap
    events = [start("L02")]
    full = P.peek(world(tmp_path / "c", events=events), making(sections=sections()))
    assert total(full) <= P.WORDS_ALL and set(full) >= {"working_on", "looking_into", "found"}


def test_found_skips_back_references(tmp_path):
    assert P.finding("[D05] (L01, claim, tier 2) The same study also timed the strike in two distinct phases. "
                     "Source: x") is None
    assert P.finding("[D09] (L01, claim, tier 2) This heron waited longer than any other bird in the study area.") is None
    assert P.finding("[D09] (L01, claim, tier 2) Too short to say much.") is None
    assert P.finding("[D09] (L01, claim, tier 2) Katzir et al. found little egrets aimed better than cattle egrets "
                     "did in tanks. Later.") == \
        "Katzir et al. found little egrets aimed better than cattle egrets did in tanks."
    # tier 3 is only for the scout's row
    rows = {"rows": [{"id": "L05", "section": 1, "idea": "x"}]}
    block = ("### L05: x\n\n[D10] (L05, claim, tier 3) Herons in Florida were seen dropping bread onto the water "
             "for fish. Source: x\n\n")
    root = world(tmp_path, events=[start("L05"), done("L05")], rows=rows, blocks=("L00",))
    (root / "04_dossier.md").write_text(HEAD + BLOCKS["L00"] + block + TAIL)
    assert P.peek(root, making(sections=sections()))["found"].startswith("Higuchi")


def test_broken_files_are_ignored(tmp_path):
    root = world(tmp_path, events=[start("L02")])
    for name in ("05_outline.json", "ledger.json", "fast/card.json"):
        (root / name).write_text("{not json")
    (root / "fast_timeline.jsonl").write_text("{broken\n" + json.dumps(start("L02")) + "\n")
    p = P.peek(root, making(sections=sections()))
    assert "question" not in p and "looking_into" not in p and "about" not in p
    assert p["working_on"] == 'part 1, "The Twig"'  # the title from status; no outline, so no lands
    (root / "04_dossier.md").unlink()
    assert "found" not in P.peek(root, making(sections=[]))
    assert isinstance(P.peek(root, making(sections="nonsense")), dict)


def below_rule(path: Path) -> str:
    return path.read_text().split("\n---\n", 1)[1]


def test_instructions_mention_the_peek():
    inst = ROOT / "voice" / "gpt_live" / "instructions"
    backend, pitch, policies = (below_rule(inst / f) for f in ("backend.md", "30_pitch.md", "80_policies.md"))
    assert "peek" in backend and "peek" in pitch and "looking into" in policies
    assert any("get_status" in line and "working on" in line for line in backend.splitlines())
    assert any("SpawnThinking" not in line and "working on" in line and "going" in line
               for line in policies.splitlines() if line.startswith("- They ask"))
    guide_lines = [line for line in below_rule(ROOT / "prompts" / "guide.md").splitlines() if "peek" in line]
    assert len(guide_lines) == 1 and "get_status" in guide_lines[0]


def test_the_last_part_never_gives_the_ending_away(tmp_path):
    """Finding 6: the last part's "lands" is the chapter's conclusion; a finding that echoes it is held back."""
    answer = "adult herons bait-fished more successfully than young ones, and observers disagree"
    outline = {**OUTLINE, "sections": [*OUTLINE["sections"][:2],
                                       {"n": 3, "title": "Learning to Wait", "lands": f"In one study {answer}."}]}
    block = ("### L03: learning\n\n[D07] (L03, claim, tier 1) Adult herons bait-fished more successfully than "
             "young ones at the Japanese site. Source: x\n\n")
    root = world(tmp_path, events=[start("L03"), done("L03")], outline=outline, blocks=("L00",))
    (root / "04_dossier.md").write_text(HEAD + BLOCKS["L00"] + block + TAIL)
    for ready in ((1, 2), ()):
        p = P.peek(root, making(first_audio=bool(ready), sections=sections(ready=ready)))
        dumped = json.dumps(p)
        assert answer not in dumped and "Adult herons" not in dumped, p
    assert P.peek(root, making(first_audio=True, sections=sections(ready=(1, 2))))["working_on"] == \
        'part 3, "The Gray Heron"'  # the status's title, and nothing after it
    # a part that is not the last keeps its lands
    assert P.peek(root, making(first_audio=True, sections=sections(ready=(1,))))["working_on"].startswith(
        'part 2, "Water That Lies": ')


def test_a_last_part_finding_that_does_not_echo_the_ending_is_a_fresh_tease(tmp_path):
    """Ian's check (2026-11-20): with the last part's findings held back, a two-part chapter teased the same part-1
    finding for three minutes. A last-part finding may go unless it echoes the last part's lands or closing idea."""
    outline = {**OUTLINE, "sections": [*OUTLINE["sections"][:2],
                                       {"n": 3, "title": "Learning to Wait",
                                        "lands": "In one study adult herons bait-fished more successfully than young ones."}]}
    block = ("### L03: learning\n\n[D07] (L03, claim, tier 1) Gray herons in Hungary were seen standing over fish ponds "
             "for up to forty minutes. Source: x\n\n")
    root = world(tmp_path, events=[start("L03"), done("L03")], outline=outline, blocks=("L00",))
    (root / "04_dossier.md").write_text(HEAD + BLOCKS["L00"] + block + TAIL)
    p = P.peek(root, making(first_audio=True, sections=sections(ready=(1, 2))))
    assert p["found"].startswith("Gray herons in Hungary"), p


def test_a_finding_leaning_on_an_unnamed_source_is_not_said():
    """The check's live run: "The paper describes two distinct phases in the head movement." became "eyes and neck"."""
    assert P.finding("[D04] (L01, claim, tier 1) The paper describes two distinct phases in the head movement "
                     "of hunting herons. Source: x") is None
    assert P.finding("[D04] (L01, claim, tier 1) A 2003 study of grey herons found two distinct phases in the "
                     "head movement. Source: x") is not None


def test_pipeline_wording_is_stripped_or_dropped():
    """Finding 7: the outline prompts' openings never reach the guide, and a claim is not "looking into"."""
    q = P.question_of
    assert q({"idea": "The section settles whether the AC panic was partly fed by Edison's side in the war of "
                      "the currents."}) == "whether the AC panic was partly fed by Edison's side in the war of the currents"
    assert q({"idea": "Settle how ImageNet's labels were made, by whom, and at what scale."}) == \
        "how ImageNet's labels were made, by whom, and at what scale"
    assert q({"idea": "Check the 2012 challenge result and margin."}) == "the 2012 challenge result and margin"
    assert q({"idea": "What the section settles is what, in MN 72, the Tathagata has let go of."}) == \
        "what, in MN 72, the Tathagata has let go of"
    assert q({"idea": "The section settles how we know the waking hour returns without artificial light."}) == \
        "how we know the waking hour returns without artificial light"
    assert q({"idea": "Herons stand still because moving would scare the fish away."}) is None  # a claim
    assert P.lands({"lands": "The section settles how light bends at the surface."}) == "how light bends at the surface"
    assert P.lands({"lands": "This section shows that herons learn."}) == "that herons learn"
    assert P.lands({"lands": "The narrator's view is that the two meet on the fuel and part on the exit."}) is None
    assert P.lands({"lands": "Where the chapter turns: people slept twice."}) is None


def test_found_rejects_research_notes_and_cut_sentences():
    """Finding 8: ids, tiers, the scout, citations, and sentences cut inside an abbreviation or a quote."""
    f = lambda text: P.finding(f"[D09] (L01, claim, tier 2) {text}")
    for note in ("Ekirch dates the shift to light before Edison, which supports D16.",
                 "The four regional counts in D45 add up to 5,055, not 6,769 in total.",
                 "The scout's entry says chicks fly free at around forty days of age.",
                 "The tuned-mass count cannot be pinned to an exact number at tier 2.",
                 "An academic paper on arXiv cites the AlexNet paper as Krizhevsky, Sutskever and Hinton, NIPS 2012.",
                 "How Brooks's reading compares with the snippet text, building on an earlier entry.",
                 "The film method is not stated on the pages I could read in the end."):
        assert f(note) is None, note
    # cut inside "N.Y.S.": the splitter no longer stops there, and the long sentence is too long to say
    assert f("A snippet of the court opinion United States Illuminating Co. v. Grant (7 N.Y.S. 35) was found by "
             "the researchers in an old reporter volume. Then more.") is None
    assert P.first_sentence("Grant (7 N.Y.S. 35) ruled on wires. Then more.") == "Grant (7 N.Y.S. 35) ruled on wires."
    # a closing quote stays with its sentence
    assert f('Les Boyd is best known among hat makers for inventing the "copulation hat." Later.') == \
        'Les Boyd is best known among hat makers for inventing the "copulation hat."'
    assert f('Les Boyd is best known among hat makers for inventing the "copulation hat') is None  # left open
    assert f("Grey herons in a Dutch study waited about twenty seconds between strikes at fish.") == \
        "Grey herons in a Dutch study waited about twenty seconds between strikes at fish."
