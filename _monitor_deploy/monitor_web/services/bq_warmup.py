"""Pré-aquecimento do cache BigQuery do portal gestores."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.test import RequestFactory

from monitor_web.services.cache_keys import bq_fingerprint, cached_peek
from monitor_web.services.home_accumulated_alerts import parse_accumulated_period
from monitor_web.services.home_fragments import (
    _map_html_for_day,
    _station_panel_windows,
    day_exceed_cache_peek,
    day_exceed_for_request,
)
from monitor_web.services.home_impact_enrich import home_impact_for_request

logger = logging.getLogger(__name__)

# Hoje + ontem: cobre MERGE com atraso de 1 dia e ingestão UTC tardia.
_WARMUP_DAILY_EXCEED_DAYS = 2


def _portal_request(path: str):
    rf = RequestFactory()
    return rf.get(path, HTTP_HOST="localhost")


def warm_portal_bq_caches(
    *,
    dataset: str = "efc_clima",
    dates: list[str] | None = None,
    accum_hours: int = 120,
    warm_map: bool = True,
) -> dict:
    """Carrega excedências, impacto e mapa Folium no cache Django (Redis compartilhado se REDIS_URL)."""
    today = date.today()
    if not dates:
        dates = [today.isoformat(), (today - timedelta(days=1)).isoformat()]

    results: dict = {"dates": {}, "errors": [], "daily_exceed": {}}
    try:
        from gcp.bq_monitor_daily import monitor_daily_exceed_table_exists, refresh_monitor_daily_exceed_range

        if monitor_daily_exceed_table_exists(dataset):
            days_back = max(_WARMUP_DAILY_EXCEED_DAYS, len(dates or [1]))
            results["daily_exceed"] = refresh_monitor_daily_exceed_range(
                dataset=dataset,
                days_back=days_back,
                end_date=dates[0] if dates else None,
            )
    except Exception as exc:
        logger.warning("warmup daily_exceed refresh: %s", exc)
        results["errors"].append(f"daily_exceed: {exc}")

    for sel in dates:
        path = f"/portal/?date={sel}&accum={accum_hours}"
        req = _portal_request(path)
        day_info: dict = {"day_exceed": False, "impact": False, "map": False, "skipped": []}
        try:
            if day_exceed_cache_peek(req, dataset, sel):
                day_info["day_exceed"] = True
                day_info["skipped"].append("day_exceed")
            else:
                df_day, *_ = day_exceed_for_request(req, dataset, sel)
                day_info["day_exceed"] = df_day is not None
        except Exception as exc:
            logger.warning("warmup day_exceed %s: %s", sel, exc)
            results["errors"].append(f"day_exceed {sel}: {exc}")
        try:
            d_start, d_end, period_key = parse_accumulated_period(req)
            fp = bq_fingerprint()
            impact_key = f"impact_day_{dataset}_{sel}_{fp}"
            if cached_peek(impact_key):
                day_info["impact"] = True
                day_info["skipped"].append("impact")
            else:
                impact = home_impact_for_request(
                    req,
                    dataset=dataset,
                    sel=sel,
                    lang_pt=True,
                    main_line=None,
                    cum_km=None,
                )
                day_info["impact"] = bool(impact.get("available"))
        except Exception as exc:
            logger.warning("warmup impact %s: %s", sel, exc)
            results["errors"].append(f"impact {sel}: {exc}")
        if warm_map:
            try:
                html, err = _map_html_for_day(
                    sel, dataset, accum_hours, lang_pt=True, embed_document=True, request=req
                )
                day_info["map"] = bool(html) and not err
            except Exception as exc:
                logger.warning("warmup map %s: %s", sel, exc)
                results["errors"].append(f"map {sel}: {exc}")
        try:
            panel = _station_panel_windows(dataset, sel)
            day_info["station_panel"] = bool(
                panel and (panel.get("station_panel_peaks") or panel.get("station_panel_entorno_peaks"))
            )
        except Exception as exc:
            logger.warning("warmup station_panel %s: %s", sel, exc)
            results["errors"].append(f"station_panel {sel}: {exc}")
            day_info["station_panel"] = False
        results["dates"][sel] = day_info

    results["ok"] = not results["errors"]
    return results
