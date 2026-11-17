from django.urls import path

from monitor_web import views

app_name = "monitor"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("internal/warmup/bq/", views.bq_warmup, name="bq_warmup"),
    path("login/", views.login_view, name="login"),
    path("login/submit/", views.login_submit, name="login_submit"),
    path("logout/", views.logout_view, name="logout"),
    path("lang/", views.set_lang, name="set_lang"),
    path("", views.home_view, name="home"),
    path("fragments/home/status/", views.home_fragment_status, name="home_fragment_status"),
    path("fragments/home/indicators/", views.home_fragment_indicators, name="home_fragment_indicators"),
    path("fragments/home/map/", views.home_fragment_map, name="home_fragment_map"),
    path("fragments/home/map-embed/", views.home_map_embed, name="home_map_embed"),
    path("fragments/home/charts/", views.home_fragment_charts, name="home_fragment_charts"),
    path("api/home/alertas-acumulados/", views.home_alertas_acumulados_api, name="home_alertas_acumulados"),
    path("api/home/raijin-alerts/", views.home_raijin_alerts_api, name="home_raijin_alerts"),
    path("api/home/climate-gfs/", views.home_climate_gfs_api, name="home_climate_gfs"),
    path("api/home/goto-km/", views.home_goto_km_api, name="home_goto_km"),
    path("api/home/charts/", views.home_charts_api, name="home_charts_api"),
    path("previsao/", views.previsao_view, name="previsao"),
    path("api/home/previsao-heatmap/", views.previsao_heatmap_api, name="previsao_heatmap_api"),
    path("api/home/previsao-charts/", views.previsao_charts_api, name="previsao_charts_api"),
    path("api/home/previsao-verification/", views.previsao_verification_api, name="previsao_verification_api"),
    path("api/home/previsao-ativos/", views.previsao_ativos_api, name="previsao_ativos_api"),
    path("boletins/", views.boletins_view, name="boletins"),
    path(
        "boletins/climatologia-era5/",
        views.boletim_era5_climatology_view,
        name="boletim_era5_climatology",
    ),
    path(
        "boletins/climatologia-era5/export/",
        views.boletim_era5_export_view,
        name="boletim_era5_export",
    ),
    path("api/boletim/dia-distribuicao/", views.boletim_dia_distribuicao_api, name="boletim_dia_distribuicao"),
    path("consultar/", views.consultar_view, name="consultar"),
    path("consultar/ask/", views.consultar_ask, name="consultar_ask"),
    path("consultar/history/", views.consultar_history_api, name="consultar_history"),
    path("consultar/clear/", views.consultar_clear, name="consultar_clear"),
    path("sensores/", views.sensores_view, name="sensores"),
    path("sobre/", views.sobre_view, name="sobre"),
]
