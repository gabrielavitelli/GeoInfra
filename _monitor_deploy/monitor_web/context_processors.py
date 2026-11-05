from __future__ import annotations

from django.conf import settings

from monitor_web.auth_service import get_profile, portal_access_level
from monitor_web.models import Audience
from monitor_web.i18n import get_lang, nav_items, t
from monitor_web.middleware import portal_base_path
from monitor_web.services.cache_keys import cached_call
from monitor_web.services.sidebar import ingestion_status_context

INGESTION_CACHE_TTL = int(getattr(settings, "MONITOR_INGESTION_CACHE_TTL", 600))
from monitor_web.services.branding import logo_context

from lib_efc import THRESHOLD_120H_MM, THRESHOLD_24H_MM, THRESHOLD_30D_MM, THRESHOLD_7D_MM


def _url_namespace(request) -> str:
    path = getattr(request, "path", "") or ""
    if path.startswith(portal_base_path()):
        return "portal"
    return "monitor"


def monitor_globals(request):
    try:
        from gcp.monitor_bq_context import monitor_active_bq_dataset

        ds = monitor_active_bq_dataset()
    except Exception:
        ds = None

    url_ns = _url_namespace(request)
    nav = nav_items(request)
    profile = get_profile(getattr(request, "user", None)) if getattr(request, "user", None) and request.user.is_authenticated else None
    access_level = portal_access_level(request.user) if url_ns == "portal" else None

    if url_ns == "portal":
        level = access_level or 1
        nav = [item for item in nav if item.get("url_name") != "consultar" or level >= 2]
        if level == 3:
            nav = [item for item in nav if item.get("url_name") != "previsao"]

    ctx = {
        "monitor_lang": get_lang(request),
        "monitor_nav": nav,
        "monitor_url_namespace": url_ns,
        "monitor_is_portal": url_ns == "portal",
        "monitor_access_level": access_level,
        "monitor_portal_hide_previsao": url_ns == "portal" and (access_level or 0) == 3,
        "monitor_dataset": ds,
        "monitor_ingestion": cached_call(
            f"ingestion_{ds or 'default'}",
            INGESTION_CACHE_TTL,
            ingestion_status_context,
            ds,
        ),
        "monitor_logo": logo_context(),
        "monitor_auth_disabled": getattr(settings, "MONITOR_AUTH_DISABLED", False),
        "monitor_limiares": [
            {"mm": int(THRESHOLD_24H_MM), "period_pt": "24 h", "period_en": "24 h"},
            {"mm": int(THRESHOLD_120H_MM), "period_pt": "5 d", "period_en": "5 d"},
            {"mm": int(THRESHOLD_7D_MM), "period_pt": "7 d", "period_en": "7 d"},
            {"mm": int(THRESHOLD_30D_MM), "period_pt": "30 d", "period_en": "30 d"},
        ],
    }
    if profile and url_ns == "portal" and profile.audience == Audience.PORTAL:
        ctx["monitor_account_expires"] = profile.valid_until
        ctx["monitor_days_until_expiry"] = profile.days_until_expiry()

    app_env = getattr(settings, "MONITOR_APP_ENV", "production")
    ctx["monitor_app_env"] = app_env
    ctx["monitor_app_env_label"] = getattr(settings, "MONITOR_APP_ENV_LABELS", {}).get(app_env, "")
    ctx["monitor_show_env_badge"] = app_env != "production"
    import os

    ctx["monitor_static_bust"] = (
        os.environ.get("MONITOR_STATIC_BUST", "").strip() or "20261105map3d2m"
    )
    return ctx
