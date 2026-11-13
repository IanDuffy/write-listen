"""Fast Start (docs/fast-start.md): a second way to run the chapter pipeline.

The opening plays a few minutes after the topic is set; the rest of the chapter is
researched, written, edited and narrated section by section while the listener listens.

    python cli.py new --seed "..." --unattended --mode fast

The overnight pipeline (stages/new.py) is untouched. This package orchestrates; the
stage functions it reuses stay in stages/.
"""
