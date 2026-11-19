"""The theme's routes (docs/theming.md).

    GET /theme.css     the active listener's theme (settings.json `theme`); ?seed=&variant=&contrast= override it
                       for a preview (the Settings picker) without saving anything
    GET /api/theme     the same as data: the setting, the choices (variants, contrasts, preset seeds) and both schemes
Saving goes through POST /api/settings {"theme": {...}} like every other setting (overnight/routes.py).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from stages import common

from . import CONTRASTS, PRESETS, VARIANTS, check, clean, css, scheme


def _defaults(cfg: dict) -> dict:
    """config.yaml listener_settings.theme (theme.DEFAULTS where it says nothing)."""
    return clean((cfg.get("listener_settings") or {}).get("theme"))


def _setting(cfg: dict, request: Request) -> dict:
    from overnight import profiles
    s = dict(profiles.settings(cfg, listener=common.listener_dir(cfg)).get("theme") or {})
    for key in ("seed", "variant", "contrast"):  # a preview: checked like a save, never stored
        v = request.query_params.get(key)
        if v is not None:
            try:
                s[key] = check(key, v)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
    return clean(s, _defaults(cfg))


def mount(app: FastAPI, cfg: dict) -> None:
    @app.get("/theme.css")
    def theme_css(request: Request) -> Response:
        return Response(css(_setting(cfg, request)), media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.get("/api/theme")
    def theme_api(request: Request) -> dict:
        s = _setting(cfg, request)
        t = scheme(s["seed"], s["variant"], s["contrast"])
        return {"settings": s, "defaults": _defaults(cfg), "variants": list(VARIANTS), "contrasts": list(CONTRASTS),
                "presets": PRESETS, "spec": t["spec"], "light": t["light"], "dark": t["dark"]}
