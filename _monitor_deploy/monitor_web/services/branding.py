from __future__ import annotations

import base64
import os
from pathlib import Path

from django.templatetags.static import static

ROOT = Path(__file__).resolve().parent.parent.parent
# Preferência: PNG hi-res do original fotográfico → JPG → SVG vetorial → legado
_STATIC_LOGO_CANDIDATES = (
    ("monitor/img/logoGeoInfra.png", "image/png"),
    ("monitor/img/logoGeoInfra.jpg", "image/jpeg"),
    ("monitor/img/logoGeoInfra-on-dark.png", "image/png"),
    ("monitor/img/logoGeoInfra.svg", "image/svg+xml"),
    ("monitor/img/geoinfra_logo.svg", "image/svg+xml"),
)


def _logo_path() -> Path | None:
    env_logo = (os.environ.get("MONITOR_LOGO_PATH") or "").strip()
    for path in (
        Path(env_logo) if env_logo else None,
        ROOT / "monitor_web" / "static" / "monitor" / "img" / "logoGeoInfra.png",
        ROOT / "monitor_web" / "static" / "monitor" / "img" / "logoGeoInfra.jpg",
        ROOT / "assets" / "logoGeoInfra.jpg",
        ROOT / "monitor_web" / "static" / "monitor" / "img" / "logoGeoInfra-on-dark.png",
        ROOT / "monitor_web" / "static" / "monitor" / "img" / "logoGeoInfra.svg",
        ROOT / "assets" / "geoinfra_logo.svg",
        ROOT / "monitor_web" / "static" / "monitor" / "img" / "geoinfra_logo.svg",
    ):
        if path is not None and path.is_file():
            return path
    return None


def logo_context() -> dict:
    """Logo GeoInfra para login e sidebar (URL estática ou data URI de fallback)."""
    alt = "GeoInfra · USP"
    for static_rel, mime in _STATIC_LOGO_CANDIDATES:
        disk = ROOT / "monitor_web" / "static" / static_rel.replace("/", os.sep)
        if disk.is_file():
            return {
                "has_logo": True,
                "logo_url": static(static_rel),
                "logo_data_uri": "",
                "logo_alt": alt,
            }
    lp = _logo_path()
    if lp is None:
        return {"has_logo": False, "logo_url": "", "logo_data_uri": "", "logo_alt": alt}
    suffix = lp.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/svg+xml")
    try:
        b64 = base64.b64encode(lp.read_bytes()).decode("ascii")
    except OSError:
        return {"has_logo": False, "logo_url": "", "logo_data_uri": "", "logo_alt": alt}
    return {
        "has_logo": True,
        "logo_url": "",
        "logo_data_uri": f"data:{mime};base64,{b64}",
        "logo_alt": alt,
    }
