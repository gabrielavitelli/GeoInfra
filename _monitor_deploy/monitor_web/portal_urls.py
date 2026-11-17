"""URLs do portal externo — prefixo configurável via MONITOR_PORTAL_PREFIX (default: portal)."""
from django.urls import path

from monitor_web import portal_views

app_name = "portal"

urlpatterns = [
    path("login/", portal_views.portal_login_view, name="login"),
    path("login/submit/", portal_views.portal_login_submit, name="login_submit"),
    path("logout/", portal_views.portal_logout_view, name="logout"),
    path("", portal_views.portal_home_view, name="home"),
    path("fragments/home/bootstrap/", portal_views.portal_fragment_bootstrap, name="home_fragment_bootstrap"),
    path("fragments/home/status/", portal_views.portal_fragment_status, name="home_fragment_status"),
    path("fragments/home/alerts/", portal_views.portal_fragment_alerts, name="home_fragment_alerts"),
    path("fragments/home/exceed/", portal_views.portal_fragment_exceed, name="home_fragment_exceed"),
    path("fragments/home/indicators/", portal_views.portal_fragment_indicators, name="home_fragment_indicators"),
    path("fragments/home/map/", portal_views.portal_fragment_map, name="home_fragment_map"),
    path("fragments/home/map-embed/", portal_views.portal_map_embed, name="home_map_embed"),
    path("fragments/home/charts/", portal_views.portal_fragment_charts, name="home_fragment_charts"),
    path("api/home/alertas-acumulados/", portal_views.portal_alertas_acumulados_api, name="home_alertas_acumulados"),
    path("api/home/raijin-alerts/", portal_views.portal_raijin_alerts_api, name="home_raijin_alerts"),
    path("api/home/climate-gfs/", portal_views.portal_climate_gfs_api, name="home_climate_gfs"),
    path("api/home/goto-km/", portal_views.portal_goto_km_api, name="home_goto_km"),
    path("api/home/charts/", portal_views.portal_charts_api, name="home_charts_api"),
    path("previsao/", portal_views.portal_previsao_view, name="previsao"),
    path("api/home/previsao-heatmap/", portal_views.portal_previsao_heatmap_api, name="previsao_heatmap_api"),
    path("api/home/previsao-charts/", portal_views.portal_previsao_charts_api, name="previsao_charts_api"),
    path("api/home/previsao-verification/", portal_views.portal_previsao_verification_api, name="previsao_verification_api"),
    path("api/home/previsao-ativos/", portal_views.portal_previsao_ativos_api, name="previsao_ativos_api"),
    path("boletins/", portal_views.portal_boletins_view, name="boletins"),
    path("sensores/", portal_views.portal_sensores_view, name="sensores"),
    path("api/boletim/dia-distribuicao/", portal_views.portal_boletim_dia_distribuicao_api, name="boletim_dia_distribuicao"),
    path("consultar/", portal_views.portal_consultar_view, name="consultar"),
    path("consultar/ask/", portal_views.portal_consultar_ask, name="consultar_ask"),
    path("consultar/history/", portal_views.portal_consultar_history, name="consultar_history"),
    path("consultar/clear/", portal_views.portal_consultar_clear, name="consultar_clear"),
    path("sobre/", portal_views.portal_sobre_view, name="sobre"),
]
