"""Fill in deploy/Caddyfile for this droplet: the domain and the admin login. setup.sh runs it with the
system python3 (standard library only) and writes the result to /etc/caddy/Caddyfile.

    WL_DOMAIN=listen.example.com WL_ADMIN_USER=ian WL_ADMIN_HASH='$2a$14$...' python3 render_caddy.py Caddyfile

Values come from the environment, not arguments, so the hash never shows in the process list."""

from __future__ import annotations

import os
import re
import sys

TEMPLATE_DOMAIN = "listen.ianduffy.com"
# The placeholder login line inside basic_auth: "<user> <bcrypt hash>".
LOGIN_LINE = re.compile(r"^(\s*)ADMIN_USER \$2a\$14\$REPLACE_WITH_THE_HASH_FROM_caddy_hash-password$", re.M)


def render(template: str, domain: str, user: str, bcrypt_hash: str) -> str:
    domain, user, bcrypt_hash = domain.strip(), user.strip(), bcrypt_hash.strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        raise ValueError(f"not a domain name: {domain!r}")
    if not re.fullmatch(r"[A-Za-z0-9._@-]+", user):
        raise ValueError("ADMIN_USER: letters, digits and . _ @ - only (it goes into the Caddyfile as is)")
    if not re.fullmatch(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}", bcrypt_hash):
        raise ValueError("the admin hash is not a bcrypt hash (expected the output of caddy hash-password)")
    if TEMPLATE_DOMAIN + " {" not in template or not LOGIN_LINE.search(template):
        raise ValueError("the Caddyfile template has changed: its domain or login placeholder is missing")
    out = template.replace(TEMPLATE_DOMAIN + " {", domain + " {", 1)
    return LOGIN_LINE.sub(lambda m: f"{m.group(1)}{user} {bcrypt_hash}", out, count=1)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        template = f.read()
    try:
        sys.stdout.write(render(template, os.environ.get("WL_DOMAIN", ""), os.environ.get("WL_ADMIN_USER", ""),
                                os.environ.get("WL_ADMIN_HASH", "")))
    except ValueError as e:
        print(f"render_caddy: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
