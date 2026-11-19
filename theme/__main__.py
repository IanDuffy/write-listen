"""`python -m theme [seed]`: print /theme.css for a seed (the default theme without one). The episode page's fallback,
for when it runs on a plain file server with no /theme.css, is made this way:

    .venv/bin/python -m theme > episode/page/theme.default.css
"""

import sys

from . import css

print(css({"seed": sys.argv[1]} if len(sys.argv) > 1 else None), end="")
