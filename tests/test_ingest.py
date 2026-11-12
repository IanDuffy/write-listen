import re

from fakes import make_pdf

from stages import ingest


def page(n, total, body):
    return (["11/8/26, 9:14 AM   Can Beauty Make Us Good? - The Atlantic"] + body +
            [f"https://www.theatlantic.com/podcasts/archive/2026/11/beauty/123456/   {n}/{total}"])


def test_ingest_strips_chrome(run, cfg):
    pages = [
        page(1, 3, ["Share", "Save", "Listen", "David Brooks: Welcome to the show. Today we ask whether",
                    "beauty can make us good.", "", "Elaine Scarry: I think it can, and I want to explain",
                    "why that is not as naive as it sounds."]),
        page(2, 3, ["David Brooks: Tell me about the Symposium and Dio-", "tima's ladder.",
                    "Elaine Scarry: Diotima says we climb from one body to all bodies."]),
        page(3, 3, ["Elaine Scarry: And that is where we end.", "", "More From The Permanent Questions",
                    "Is Love a Choice?", "What Do We Owe Strangers?"]),
    ]
    src = run.root / "upload.pdf"
    make_pdf(src, pages)
    ingest.run(run, cfg, source=str(src))
    text = run.path("transcript").read_text()
    removed = (run.path("source_dir") / "removed_lines.txt").read_text()

    assert run.path("transcript_pdf").exists()
    assert "The Atlantic" not in text and "theatlantic.com" not in text
    assert "Share" not in text.split() and "Is Love a Choice?" not in text
    assert "David Brooks: Welcome to the show. Today we ask whether beauty can make us good." in text
    assert "Dio- tima" not in text and re.search(r"Diotima.s ladder\.", text)
    assert text.count("Elaine Scarry:") == 3
    assert "[trailing related block] Is Love a Choice?" in removed
    assert "[site chrome] Share" in removed


TH = ""  # The Atlantic's web font draws the capital 'Th' ligature from this private-use code point


def test_ingest_restores_th_ligatures(run, cfg, tmp_path):
    # Regression: pypdf returned the 'Th' ligature as an invisible private-use character, so
    # 'These' read 'ese' and 'The' read 'e'; ff/ffi ligature characters were left as single glyphs.
    txt = tmp_path / "extracted.txt"
    txt.write_text(
        f"Brooks: {TH}ese are enduring questions, and these are mine. {TH}e following is the start.\n"
        f"Scarry: {TH}at is true, and that is a diﬀerent and diﬃcult point.\n"
        f"Brooks: {TH}ere is a danger that is: t{TH}at belief.\n"
        "Scarry: Odd glyphs:  and soware.\n", encoding="utf-8")
    ingest.run(run, cfg, source=str(txt))
    text = run.path("transcript").read_text(encoding="utf-8")
    removed = (run.path("source_dir") / "removed_lines.txt").read_text(encoding="utf-8")

    assert "These are enduring questions" in text and "The following is" in text
    assert "That is true" in text and "There is a danger" in text
    assert "different and difficult" in text
    assert "is: tThat belief" in text  # a typo in the source stays as printed...
    assert re.search(r"U\+E053 restored as 'Th' inside a word, check: \.\.\..*t<\?>at belief", removed)  # ...flagged
    assert not re.search(r"[-ﬀ-ﬆ]", text)
    assert "U+E053 restored as 'Th' (5 times" in removed
    # Private-use characters that are not 'Th' are dropped and listed for a human, never guessed.
    assert "U+E900 unrecognized, dropped" in removed and "U+E111 unrecognized, dropped" in removed
    assert "soware" in text


def th_text(n=6):
    spelled = "Scarry: I think that the point is that these things matter, and there they are. "
    return spelled + " ".join(f"{TH}at is line {i}. {TH}e end of {i}." for i in range(n))


def test_one_odd_th_use_does_not_veto_the_rest():
    # Regression: a single standalone or accented use made every genuine 'Th' get dropped.
    text, notes = ingest.repair_glyphs(th_text() + f" Le {TH}éâtre. Icon: {TH}\n")
    assert text.count("That is line") == 6 and text.count("The end of") == 6
    assert "Théâtre" in text and "Icon: \n" in text
    assert any("U+E053 dropped, check:" in n and "Icon: <?>" in n for n in notes)


def test_mid_word_ligature_is_not_th():
    # Regression: a discretionary 'ft' ligature (o+glyph+en = 'often') was restored as 'Th' because
    # 'then' is a word; position was never checked.
    ft = ""
    text, notes = ingest.repair_glyphs(f"We o{ft}en talk. And then we o{ft}en stop. A{ft}er that, then what?")
    assert "Th" not in text and "oen talk" in text
    assert any("U+E020 unrecognized, dropped (3 times)" in n for n in notes)


def test_lone_code_point_is_flagged_not_silent():
    # Regression: a code point seen once was restored on one matching word with no sample to check.
    fl = ""
    text, notes = ingest.repair_glyphs(th_text() + f"\nBrooks: The field was {fl}at and wide. Then {fl}ying.")
    assert "was That and" in text  # one use forming a common word is trusted...
    assert any("U+E030 restored as 'Th' on little evidence, check:" in n and "field was <?>at and wide." in n
               for n in notes)  # ...and listed for a human to check
    assert "Then ying." in text  # 'thying' is neither common nor spelled out: dropped...
    assert any("U+E030 dropped, check:" in n and "Then <?>ying." in n for n in notes)  # ...and listed


def test_rare_code_point_judges_each_use():
    # Regression: with fewer than 3 uses, one word outside COMMON_TH ('Thought') dropped every use,
    # 'The' included, and only the first dropped use was listed.
    it = ""  # the italic intro's own 'Th' code point in the real PDF
    body = " Scarry: I thought about three things there, and the rest."
    text, notes = ingest.repair_glyphs(f"{it}e following is a transcript. {it}ought experiments.{body}")
    assert text.startswith("The following is a transcript. Thought experiments.")
    assert sum("U+E001 restored as 'Th' on little evidence, check:" in n for n in notes) == 2


def test_clean_strips_print_page_chrome():
    # Regression: share counts, sign-in buttons, a subscription promo, podcast-platform links and a
    # photo credit survived, and "View Discussion" blocked the cut at ABOUT THE AUTHOR.
    first = ["THE PERMANENT QUESTIONS", "Can Beauty Make Us Good?", "By David Brooks",
             "David Brooks and Elaine Scarry (The Atlantic)", "NOVEMBER 8, 2026 DISCUSS SAVE",
             "A gift that gets them: Give an Atlantic subscription, plus a free tote.*",
             "SHARE 66", "Sign In Subscribe"]
    middle = ["Subscribe here:  Apple Podcasts | Spotify | YouTube",
              "David Brooks: Today's episode is about beauty.",
              "Scarry: I once got a gift subscription to a magazine, and I loved it.",
              "Brooks: Share 3 things you love."] + [f"Scarry: Line {i} of the talk." for i in range(70)]
    last = ["Brooks: See you next week.", "View Discussion", "ABOUT THE AUTHOR", "66", "David Brooks",
            "David Brooks is a staff writer at The Atlantic.", "Follow"]
    footer = ["About", "Contact", "Privacy Policy Your Privacy Choices", "Do Not Sell Or Share My Personal Information"]
    text, removed = ingest.clean(["\n".join(first), "\n".join(middle), "\n".join(last), "\n".join(footer)])

    for chrome in ["(The Atlantic)", "DISCUSS", "free tote", "SHARE 66", "Sign In", "Apple Podcasts",
                   "View Discussion", "staff writer", "Privacy Policy"]:
        assert chrome not in text, chrome
    assert "NOVEMBER 8, 2026" in text and "Can Beauty Make Us Good?" in text
    assert "David Brooks: Today's episode is about beauty." in text
    assert "I once got a gift subscription to a magazine" in text  # dialogue, not a promo
    assert "Brooks: Share 3 things you love." in text
    assert "Brooks: See you next week." in text
    assert "[trailing related block] ABOUT THE AUTHOR" in removed


def test_chrome_patterns_leave_dialogue_alone():
    # Regression: the credit, subscribe and promo patterns matched fragments, so these wrapped
    # dialogue lines and speaker turns were deleted as site chrome.
    dialogue = [
        "Brooks: I made a version of that argument once (The Atlantic, 2019)",
        "Scarry: I first read Plato in high school (AP English, of all places)",
        "we crossed it by boat in the summer (the Atlantic Ocean)",
        "follow the money, it all ends up in one place: Spotify. So where's beauty?",
        "listen to it read aloud, they ask where: YouTube, they mean. And then they",
        "Brooks: My mother would give everyone a subscription to Poetry for free",
        "Scarry: Some people give up their subscription to the idea of free will.",
        "so they give their subscription away for free, and that is generous",
    ]
    for line in dialogue:
        assert not ingest.is_chrome(line), line
    for line in ["SHARE 66", "Sign In Subscribe", "View Discussion", "David Brooks and Elaine Scarry (The Atlantic)",
                 "Subscribe here:  Apple Podcasts | Spotify | YouTube",
                 "A gift that gets them: Give an Atlantic subscription, plus a free tote.*"]:
        assert ingest.is_chrome(line), line


def test_wrapped_sentence_ending_in_a_label_word_stays():
    # Regression: the period was stripped before the label lookup, so a sentence wrapped onto
    # its own line as "The Atlantic." was deleted, cutting the sentence mid-way.
    text, removed = ingest.clean(["Brooks: Elaine wrote an important essay about this for\nThe Atlantic.\n"
                                  "Scarry: Yes, and it was a long time coming."])
    assert "essay about this for The Atlantic." in text and not removed


def test_platform_list_on_its_own_line_is_chrome():
    # Regression: a platform list split from its "Listen and subscribe:" label, or wrapped with a
    # trailing pipe, leaked into the episode intro.
    text, removed = ingest.clean(["Can Beauty Make Us Good?\nListen and subscribe:\n"
                                  "Apple Podcasts | Spotify | YouTube | Overcast\n\nWhat is beauty?\n"
                                  "Subscribe here: Apple Podcasts | Spotify | YouTube |\n\n"
                                  "David Brooks: Today's episode is about Spotify | YouTube culture."])
    assert "Overcast" not in text and "Subscribe here" not in text
    assert "David Brooks: Today's episode is about Spotify | YouTube culture." in text


def test_wrapped_follow_does_not_block_the_bio_cut():
    # Regression: the prev tracker skipped lowercase chrome words, so a wrapped "follow." meant the
    # line before it ("...we all should") did not end a sentence and the bio was never cut.
    pages = ["\n".join(f"Scarry: Line {i} of the talk." for i in range(70)),
             "Brooks: That is the path that I think we all should\nfollow.\n\nAbout the Author\n"
             "David Brooks is a staff writer at The Atlantic."]
    text, removed = ingest.clean(pages)
    assert "we all should follow." in text and "staff writer" not in text
    assert "[trailing related block] About the Author" in removed
