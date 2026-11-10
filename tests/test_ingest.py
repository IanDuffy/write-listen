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
