"""The theme's routes (docs/theming.md).

    GET /theme.css     the active listener's theme (settings.json `theme`); ?seed=&variant=&contrast=&mode= override
                       it for a preview (the theme bar, the Settings picker) without saving anything
    GET /api/theme     the same as data: the setting, the choices (looks, variants, contrasts, modes) and both schemes
    GET /theme.js      page/theme.js: the theme bar (five looks; light, dark or auto) and window.Theme, which applies
                       a theme on any page
Saving goes through POST /api/settings {"theme": {...}} like every other setting (overnight/routes.py).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from stages import common

from . import CONTRASTS, LOOKS, MODES, VARIANTS, check, clean, css, scheme

THEME_JS = common.ROOT / "page" / "theme.js"


def _looks(contrast: str) -> list[dict]:
    """The five looks, each with the colour its swatch shows: the look's primary, light and dark, at this contrast."""
    out = []
    for look in LOOKS:
        t = scheme(look["seed"], look["variant"], contrast)
        out.append({**look, "swatch": {"light": t["light"]["primary"], "dark": t["dark"]["primary"]}})
    return out


def _defaults(cfg: dict) -> dict:
    """config.yaml listener_settings.theme (theme.DEFAULTS where it says nothing)."""
    return clean((cfg.get("listener_settings") or {}).get("theme"))


def _setting(cfg: dict, request: Request) -> dict:
    from overnight import profiles
    s = dict(profiles.settings(cfg, listener=common.listener_dir(cfg)).get("theme") or {})
    for key in ("seed", "variant", "contrast", "mode"):  # a preview: checked like a save, never stored
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
                "modes": list(MODES), "looks": _looks(s["contrast"]), "spec": t["spec"], "light": t["light"],
                "dark": t["dark"]}

    @app.get("/theme.js")
    def theme_js() -> FileResponse:
        return FileResponse(THEME_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})
