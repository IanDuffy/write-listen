"""tests/signals: Listener Signals. The tester fakes live in tests/demo, which is on sys.path only once pytest
has collected it; put it there so these tests also run alone."""
import sys
from pathlib import Path

DEMO = str(Path(__file__).resolve().parent.parent / "demo")
if DEMO not in sys.path:
    sys.path.insert(0, DEMO)
