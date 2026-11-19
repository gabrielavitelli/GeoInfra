"""Matriz de permissões — portal nível 1 / 2 / 3."""
from __future__ import annotations

from monitor_web.auth_service import (
    get_profile,
    portal_access_level,
    portal_session_access_level,
    portal_session_is_valid,
)
from monitor_web.models import Audience

# url_name do namespace portal -> nível mínimo exigido
PORTAL_VIEW_MIN_LEVEL: dict[str, int] = {
    "home": 1,
    "home_fragment_bootstrap": 1,
    "home_fragment_status": 1,
    "home_fragment_alerts": 1,
    "home_fragment_exceed": 1,
    "home_fragment_indicators": 1,
    "home_fragment_map": 1,
    "home_map_embed": 1,
    "home_fragment_charts": 1,
    "home_alertas_acumulados": 1,
    "home_raijin_alerts": 1,
    "home_climate_gfs": 1,
    "home_goto_km": 1,
    "home_charts_api": 1,
    "home_sensor_series": 1,
    "previsao": 1,
    "previsao_heatmap_api": 1,
    "previsao_charts_api": 1,
    "previsao_verification_api": 1,
    "previsao_ativos_api": 1,
    "boletins": 1,
    "boletim_dia_distribuicao": 1,
    "sobre": 1,
    "sensores": 1,
    "consultar": 2,
    "consultar_ask": 2,
    "consultar_history": 2,
    "consultar_clear": 2,
}

PORTAL_PREVISAO_URL_NAMES: frozenset[str] = frozenset(
    {
        "previsao",
        "previsao_heatmap_api",
        "previsao_charts_api",
        "previsao_verification_api",
        "previsao_ativos_api",
    }
)


def portal_previsao_blocked(profile) -> bool:
    """Nível 3: portal com Raijin, sem aba/API de Previsão."""
    return (
        profile is not None
        and profile.audience == Audience.PORTAL
        and int(profile.access_level or 1) == 3
    )


def portal_effective_level(raw_level: int) -> int:
    """Nível 3 equivale a 2 para Raijin; Previsão é bloqueada separadamente."""
    level = int(raw_level or 0)
    return 2 if level == 3 else level


def portal_min_level_for_url_name(url_name: str | None) -> int:
    if not url_name:
        return 1
    return PORTAL_VIEW_MIN_LEVEL.get(url_name, 2)


def user_can_access_portal_view(user, url_name: str | None) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = get_profile(user)
    if profile is None:
        return False
    if profile.audience != Audience.PORTAL:
        return False
    if not profile.is_account_valid():
        return False
    if portal_previsao_blocked(profile) and url_name in PORTAL_PREVISAO_URL_NAMES:
        return False
    need = portal_min_level_for_url_name(url_name)
    return portal_effective_level(int(profile.access_level or 1)) >= need


def request_can_access_portal_view(request, user, url_name: str | None) -> bool:
    """Permissões portal: perfil no DB ou claims na sessão assinada (multi-instância Cloud Run)."""
    if user_can_access_portal_view(user, url_name):
        return True
    if not portal_session_is_valid(request):
        return False
    session_level = portal_session_access_level(request)
    if session_level == 3 and url_name in PORTAL_PREVISAO_URL_NAMES:
        return False
    need = portal_min_level_for_url_name(url_name)
    return portal_effective_level(session_level) >= need
