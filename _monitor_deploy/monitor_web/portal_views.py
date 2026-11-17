"""Views do portal externo — reutiliza lógica do monitor operacional."""
from __future__ import annotations

from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from monitor_web import views as monitor_views
from monitor_web.auth_service import (
    AuthResult,
    authenticate_monitor,
    get_profile,
    login_monitor,
    logout_monitor,
)
from monitor_web.models import Audience
from monitor_web.services.branding import logo_context


def _portal_login_context(request, *, error: str | None = None) -> dict:
    profile = get_profile(getattr(request, "user", None))
    ctx = {
        "error": error,
        "portal_title": "Portal GeoInfra",
        "portal_subtitle": "Monitoramento Climático EFC",
        **logo_context(),
    }
    if profile and profile.audience == Audience.PORTAL and profile.is_account_valid():
        ctx["days_until_expiry"] = profile.days_until_expiry()
    return ctx


@ensure_csrf_cookie
@require_GET
def portal_login_view(request):
    if getattr(request, "user", None) and request.user.is_authenticated:
        from monitor_web.auth_service import is_portal_user

        if is_portal_user(request.user) or request.user.is_staff:
            return redirect("portal:home")
    return render(request, "monitor/portal_login.html", _portal_login_context(request))


@require_POST
def portal_login_submit(request):
    u = (request.POST.get("username") or "").strip()
    p = request.POST.get("password") or ""
    result: AuthResult = authenticate_monitor(u, p, expected_audience=Audience.PORTAL)
    if not result.ok or result.user is None:
        return render(
            request,
            "monitor/portal_login.html",
            _portal_login_context(request, error=result.error or "Usuário ou senha inválidos."),
        )
    login_monitor(request, result.user)
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt.startswith("/"):
        return redirect(nxt)
    return redirect("portal:home")


@require_POST
def portal_logout_view(request):
    logout_monitor(request)
    return redirect("portal:login")


@require_GET
def portal_home_view(request):
    return monitor_views.home_view(request)


@require_GET
def portal_previsao_view(request):
    return monitor_views.previsao_view(request)


@require_GET
def portal_boletins_view(request):
    return monitor_views.boletins_view(request)


@require_GET
def portal_sensores_view(request):
    return monitor_views.sensores_view(request)


@require_GET
def portal_sobre_view(request):
    return monitor_views.sobre_view(request)


@require_GET
def portal_consultar_view(request):
    return monitor_views.consultar_view(request)


@require_POST
def portal_consultar_ask(request):
    return monitor_views.consultar_ask(request)


@require_GET
def portal_consultar_history(request):
    return monitor_views.consultar_history_api(request)


@require_POST
def portal_consultar_clear(request):
    return monitor_views.consultar_clear(request)


# Fragments / APIs — delegação direta
portal_fragment_bootstrap = monitor_views.home_fragment_bootstrap
portal_fragment_status = monitor_views.home_fragment_status
portal_fragment_alerts = monitor_views.home_fragment_alerts
portal_fragment_exceed = monitor_views.home_fragment_exceed
portal_fragment_indicators = monitor_views.home_fragment_indicators
portal_fragment_map = monitor_views.home_fragment_map
portal_map_embed = monitor_views.home_map_embed
portal_fragment_charts = monitor_views.home_fragment_charts
portal_alertas_acumulados_api = monitor_views.home_alertas_acumulados_api
portal_raijin_alerts_api = monitor_views.home_raijin_alerts_api
portal_climate_gfs_api = monitor_views.home_climate_gfs_api
portal_goto_km_api = monitor_views.home_goto_km_api
portal_charts_api = monitor_views.home_charts_api
portal_previsao_heatmap_api = monitor_views.previsao_heatmap_api
portal_previsao_charts_api = monitor_views.previsao_charts_api
portal_previsao_verification_api = monitor_views.previsao_verification_api
portal_previsao_ativos_api = monitor_views.previsao_ativos_api
portal_boletim_dia_distribuicao_api = monitor_views.boletim_dia_distribuicao_api
