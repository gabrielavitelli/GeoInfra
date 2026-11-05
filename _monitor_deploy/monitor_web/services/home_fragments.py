"""Fragmentos HTMX da Home — BQ otimizado por estágio."""
from __future__ import annotations

import concurrent.futures
import logging
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
from django.core.cache import cache

from lib_efc import (
    THRESHOLD_120H_MM,
    THRESHOLD_120H_WARNING_MM,
    THRESHOLD_24H_MM,
    THRESHOLD_30D_MM,
    THRESHOLD_7D_MM,
    limiar_kind_from_label,
    ANA_ADOTADA_CSV,
    ANA_ESTACOES_CSV,
    BNDMET_DADOS_CSV,
    BNDMET_ESTACOES_CSV,
    get_merge_along_railway_stats,
)
from monitor_web.i18n import get_lang
from monitor_web.services.cache_keys import (
    bq_fingerprint,
    cached_call,
    cached_call_swr,
    cached_peek,
    map_folium_cache_key,
    map_folium_stable_cache_key,
)
from monitor_web.services.home import (
    _cached_station_accum,
    _cached_station_catalog,
    _csv_mtime,
    _map_load_timeout_message,
    _map_unavailable_message,
    _operational_map_html,
    _railway,
    _ttl,
)
from monitor_web.services.home_impact_enrich import home_impact_for_request
from monitor_web.services.home_indicators_html import indicators_panel_html
from monitor_web.services.map_alert_markers import (
    alert_markers_from_df,
    operational_day_alert_markers_from_df,
)
from monitor_web.services.status_hero import status_hero_context
from monitor_web.services.html_ui import dashboard_panel_header, kpi_card, map_help_context
from monitor_web.services.plotly_charts import fig_to_json

logger = logging.getLogger(__name__)

MAP_CACHE_TTL = int(os.environ.get("MONITOR_MAP_CACHE_TTL", "3600"))
MAP_BUILD_TIMEOUT_SEC = int(os.environ.get("MONITOR_MAP_BUILD_TIMEOUT_SEC", "90"))
# Pontos clicáveis de km: 10 ≈ metade dos markers vs step=5 (UX ainda ok).
MAP_KM_STEP = max(2, int(os.environ.get("MONITOR_MAP_KM_STEP", "10") or "10"))
# Bump when Folium render format changes (e.g. inline vs Jupyter srcdoc iframe).
# v53: suscetibilidade raster 4096px (pixels GeoTIFF); inclui bust via env.
_susc_bust = (os.environ.get("SUSCETIBILIDADE_CACHE_BUST") or "").strip()
# v56: lazy 3D + parallel catalogs + clima_series-only map accum (fast cold).
MAP_CACHE_VER = (os.environ.get("MONITOR_MAP_CACHE_VER") or "folium_v56_mapfast").strip()
if _susc_bust:
    MAP_CACHE_VER = f"{MAP_CACHE_VER}_{_susc_bust}"

STATION_PANEL_TTL = int(os.environ.get("MONITOR_STATION_PANEL_TTL", "1800") or "1800")
STATION_ROWS_TTL = int(os.environ.get("MONITOR_STATION_ROWS_TTL", "900") or "900")


def parse_fc_km(raw, *, default: int = 379, km_max: int = 900) -> int:
    """Km do formulário Home — tolera vazio ou valor inválido."""
    if raw is None or str(raw).strip() == "":
        v = default
    else:
        try:
            v = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            v = default
    return max(0, min(int(km_max), v))


def _active_dataset() -> str:
    try:
        from gcp.monitor_bq_context import monitor_active_bq_dataset

        return monitor_active_bq_dataset()
    except Exception:
        return os.environ.get("BQ_DATASET", "efc_clima")


def parse_home_params(request) -> dict:
    """Parâmetros comuns date / accum / lang / dataset."""
    lang_pt = get_lang(request) == "pt"
    dataset = _active_dataset()
    today = date.today()
    selected = request.GET.get("date") or today.isoformat()
    try:
        chosen = pd.Timestamp(selected).date()
    except Exception:
        chosen = today
    from monitor_web.services.accum_window import parse_accum_hours

    accum_hours = parse_accum_hours(request.GET.get("accum"))
    main_line, cum_km, _, _ = _railway()
    km_max = int(np.ceil(float(cum_km[-1]))) if cum_km is not None and len(cum_km) else 900
    km_raw = request.GET.get("km")
    has_km_focus = km_raw is not None and str(km_raw).strip() != ""
    return {
        "lang_pt": lang_pt,
        "dataset": dataset,
        "chosen": chosen,
        "selected_date": chosen.isoformat(),
        "selected_display": chosen.strftime("%d/%m/%Y"),
        "accum_hours": accum_hours,
        "km_max": km_max,
        "main_line": main_line,
        "cum_km": cum_km,
        "fc_km": parse_fc_km(km_raw, default=379, km_max=km_max),
        "has_km_focus": has_km_focus,
    }


def bq_locations_probe(dataset: str) -> bool:
    """Health check BQ sem carregar série de 45d."""

    def _probe() -> bool:
        try:
            from google.cloud import bigquery
            from gcp.bq_config import EFC_RAILWAY_LOCATIONS_WHERE, get_bq_project_id

            client = bigquery.Client(
                project=os.environ.get("GCP_PROJECT")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or get_bq_project_id()
            )
            proj = client.project
            full_locations = f"{proj}.{dataset}.locations"
            sql = f"""
            SELECT 1 AS ok FROM `{full_locations}`
            WHERE {EFC_RAILWAY_LOCATIONS_WHERE}
            LIMIT 1
            """
            # .result() evita BigQuery Storage API (readsessions.create).
            return bool(list(client.query(sql).result()))
        except Exception:
            return False

    fp = bq_fingerprint()
    return bool(cached_call(f"loc_probe_{dataset}_{fp}", 86400, _probe))


def _clamp_date_to_bq_range(chosen: date, dataset: str) -> tuple[date, str | None, str | None]:
    from monitor_web.services.bq_cache import get_bq_date_range_cached

    bq_lo, bq_hi = get_bq_date_range_cached(dataset=dataset)
    if bq_lo and bq_hi:
        cal_min, cal_max = pd.Timestamp(bq_lo).date(), pd.Timestamp(bq_hi).date()
        if chosen < cal_min:
            chosen = cal_min
        if chosen > cal_max:
            chosen = cal_max
    return chosen, bq_lo, bq_hi


def load_day_bq(dataset: str, ref_date: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Série + locations só para o dia selecionado (sem janela 60d)."""
    fp = bq_fingerprint()

    def _load():
        from gcp.bq_reader_clima import load_efc_precip_from_bq_date_range

        return load_efc_precip_from_bq_date_range(
            dataset=dataset, source="all", date_start=ref_date, date_end=ref_date
        )

    return cached_call(f"home_day_{dataset}_{ref_date}_{fp}", _ttl(), _load)


def load_exceed_window_bq(dataset: str, ref_date: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Série ferrovia ~30 dias para limiares 7 d / 30 d no dia selecionado."""
    ref = str(ref_date)[:10]
    lookback = (pd.Timestamp(ref) - pd.Timedelta(days=29)).strftime("%Y-%m-%d")
    fp = bq_fingerprint()

    def _load():
        from gcp.bq_reader_clima import load_efc_precip_from_bq_date_range

        return load_efc_precip_from_bq_date_range(
            dataset=dataset, source="all", date_start=lookback, date_end=ref
        )

    return cached_call(f"home_exceed_win_{dataset}_{lookback}_{ref}_{fp}", _ttl(), _load)


def _try_day_exceed_fast_table(dataset: str, ref_date: str, *, alerts_source: str):
    """Portal: lê monitor_daily_exceed (pré-agregado) em vez de scan em clima_series.

    Retorna None quando a tabela não tem linhas para o dia — assim o caller
    cai no cálculo live (BQ). DataFrame vazio não é cache 'hit' útil.
    """
    if alerts_source != "merge":
        return None
    try:
        from gcp.bq_monitor_daily import load_monitor_daily_exceed_df, monitor_daily_exceed_enabled

        if not monitor_daily_exceed_enabled():
            return None
        df_day = load_monitor_daily_exceed_df(ref_date, dataset=dataset, source="merge")
        if df_day is None or df_day.empty:
            return None
        return df_day, df_day, None, None, ((), ())
    except Exception as exc:
        logger.warning("monitor_daily_exceed fast path failed: %s", exc)
        return None


def _day_merge_light_impl(dataset: str, ref_date: str):
    """Excedências do dia (24h/5d) — uma query BQ, sem janela de 30 dias (portal)."""
    fast = _try_day_exceed_fast_table(dataset, ref_date, alerts_source="merge")
    if fast is not None:
        return fast

    from gcp.bq_reader_clima import (
        points_above_from_bq_for_date,
        points_almost_120h_from_bq_for_date,
        table_exceedance_from_bq,
    )

    bq_day, bq_locs = load_day_bq(dataset, ref_date)
    if bq_locs is None:
        return None, None, None, None, None
    if bq_day is None:
        bq_day = pd.DataFrame()
    df_day = table_exceedance_from_bq(
        bq_day,
        bq_locs,
        THRESHOLD_24H_MM,
        THRESHOLD_120H_MM,
    )
    ref = str(ref_date)[:10]
    if not df_day.empty and "Dia" in df_day.columns:
        df_day = df_day[df_day["Dia"].astype(str).str[:10] == ref].copy()
    pts_24 = points_above_from_bq_for_date(bq_day, bq_locs, ref, "precip_24h_mm", THRESHOLD_24H_MM)
    pts_120 = points_almost_120h_from_bq_for_date(
        bq_day, bq_locs, ref, THRESHOLD_120H_WARNING_MM, THRESHOLD_120H_MM
    )
    return df_day, df_day, bq_day, bq_locs, (pts_24, pts_120)


def _day_exceed_and_points_impl(dataset: str, ref_date: str, *, alerts_source: str, light: bool = False):
    from gcp.bq_raijin import get_raijin_day_alerts_df

    if alerts_source == "raijin":
        df_day = get_raijin_day_alerts_df(dataset=dataset, ref_date=ref_date)
        if df_day is None:
            df_day = pd.DataFrame()
        # Fonte Raijin: só TP. Vazio = sem ocorrência confirmada (não cai no MERGE).
        return df_day, df_day, None, None, ((), ())

    if light:
        return _day_merge_light_impl(dataset, ref_date)

    fast = _try_day_exceed_fast_table(dataset, ref_date, alerts_source=alerts_source)
    if fast is not None:
        return fast

    from gcp.bq_reader_clima import (
        points_above_from_bq_for_date,
        points_almost_120h_from_bq_for_date,
        table_exceedance_operational_from_bq,
    )

    bq_series, bq_locs = load_exceed_window_bq(dataset, ref_date)
    bq_day, _ = load_day_bq(dataset, ref_date)
    if bq_locs is None:
        return None, None, None, None, None
    if bq_series is None:
        bq_series = pd.DataFrame()
    if bq_day is None:
        bq_day = pd.DataFrame()
    df_day = table_exceedance_operational_from_bq(
        bq_series,
        bq_locs,
        ref_date,
        THRESHOLD_24H_MM,
        THRESHOLD_120H_MM,
        THRESHOLD_7D_MM,
        THRESHOLD_30D_MM,
    )
    df_exceed = df_day
    pts_24 = points_above_from_bq_for_date(bq_day, bq_locs, ref_date, "precip_24h_mm", THRESHOLD_24H_MM)
    pts_120 = points_almost_120h_from_bq_for_date(
        bq_day, bq_locs, ref_date, THRESHOLD_120H_WARNING_MM, THRESHOLD_120H_MM
    )
    return df_day, df_exceed, bq_series, bq_locs, (pts_24, pts_120)


def alerts_source_for_request(request) -> str:
    """Portal gestores: excedências MERGE (limiares operacionais). Interno: env MONITOR_ALERTS_SOURCE."""
    import os

    from monitor_web.template_paths import is_portal_request

    if is_portal_request(request):
        return (os.environ.get("MONITOR_PORTAL_ALERTS_SOURCE") or "merge").strip().lower()
    from gcp.bq_raijin import monitor_alerts_source

    return monitor_alerts_source()


def alerts_panel_heading(alerts_source: str, *, lang_pt: bool, n_kms: int = 0) -> str:
    """Título do painel lateral — deixa explícita a fonte (Raijin vs MERGE)."""
    src = (alerts_source or "merge").strip().lower()
    if src == "raijin":
        if lang_pt:
            base = "Alertas Raijin"
            return f"{base} ({n_kms} km)" if n_kms else base
        base = "Raijin Alerts"
        return f"{base} ({n_kms} km)" if n_kms else base
    if lang_pt:
        base = "Trechos em Alerta"
        return f"{base} ({n_kms} km)" if n_kms else base
    base = "Alert Segments"
    return f"{base} ({n_kms} km)" if n_kms else base


def exceed_table_heading(
    alerts_source: str, *, lang_pt: bool, selected_display: str | None = None
) -> str:
    src = (alerts_source or "merge").strip().lower()
    day = (selected_display or "").strip()
    if src == "raijin":
        if lang_pt:
            return f"Alertas Raijin · {day}" if day else "Alertas Raijin"
        return f"Raijin alerts · {day}" if day else "Raijin alerts"
    if lang_pt:
        return f"Excesso de limites · {day}" if day else "Excesso de limites"
    return f"Threshold exceedances · {day}" if day else "Threshold exceedances"


def exceed_table_from_day_df(
    df_day, *, dia_label: str | None = None
) -> list[dict]:
    """Tabela do dia: com fonte Raijin, só TP (mapa/hero). Dia para a coluna Data."""
    from lib_efc import group_exceed_table_rows
    from monitor_web.services.suscetibilidade import filter_raijin_tp_df

    table_df = filter_raijin_tp_df(df_day)
    if table_df is None or table_df.empty:
        return []
    raw_rows = [
        {
            "Limiar": r.get("Limiar"),
            "Km": r.get("Quilometragem (km)", r.get("Quilometragem")),
            "Valor": r.get("Valor (mm)", r.get("Valor")),
            "Dia": r.get("Dia") or dia_label,
        }
        for r in table_df.to_dict("records")
    ]
    rows = group_exceed_table_rows(raw_rows)
    if dia_label:
        for row in rows:
            if not row.get("Dia"):
                row["Dia"] = dia_label
    return rows


def build_exceed_table_for_status(
    *,
    df_day,
    dataset: str,
    ref_date: str,
    selected_display: str,
    alerts_source: str,
) -> list[dict]:
    """TP do dia + FN (não detectado) quando a fonte é Raijin."""
    rows = exceed_table_from_day_df(df_day, dia_label=selected_display)
    if (alerts_source or "").strip().lower() == "raijin":
        rows = list(rows) + _raijin_fn_table_rows(
            dataset, ref_date, dia_label=selected_display
        )
    return rows


def _raijin_fn_table_rows(dataset: str, ref_date: str, *, dia_label: str | None = None) -> list[dict]:
    """Linhas FN (não detectado / miss) do dia selecionado."""
    fp = bq_fingerprint()
    key = f"raijin_fn_day_v1_{dataset}_{str(ref_date)[:10]}_{fp}"

    def _load() -> list[dict]:
        from gcp.bq_raijin import get_raijin_day_fn_rows

        return get_raijin_day_fn_rows(dataset=dataset, ref_date=str(ref_date)[:10])

    try:
        hits = cached_call(key, 300, _load) or []
    except Exception as exc:
        logger.warning("raijin_fn_table failed: %s", exc)
        return []
    out: list[dict] = []
    for hit in hits:
        out.append(
            {
                "Limiar": "Não detectado",
                "Km": str(int(hit.get("km") or 0)),
                "Valor": hit.get("mm"),
                "Dia": dia_label,
            }
        )
    return out


def _day_exceed_and_points(
    dataset: str,
    ref_date: str,
    *,
    alerts_source: str | None = None,
    light: bool = False,
):
    """Cache único por dia — status, mapa e indicadores reutilizam a mesma carga BQ."""
    from gcp.bq_raijin import monitor_alerts_source

    ref = str(ref_date)[:10]
    src = (alerts_source or monitor_alerts_source()).strip().lower()
    fp = bq_fingerprint()
    lite = "lite" if light else "full"

    def _load():
        return _day_exceed_and_points_impl(dataset, ref, alerts_source=src, light=light)

    return cached_call(f"day_exceed_pts_v5_{src}_{lite}_{dataset}_{ref}_{fp}", _ttl(), _load)


def day_exceed_for_request(request, dataset: str, ref_date: str, *, light: bool | None = None):
    from monitor_web.template_paths import is_portal_request

    src = alerts_source_for_request(request)
    use_light = light
    if use_light is None:
        use_light = is_portal_request(request) and src == "merge"
    return _day_exceed_and_points(dataset, ref_date, alerts_source=src, light=use_light)


def day_exceed_for_request_safe(request, dataset: str, ref_date: str, *, light: bool | None = None):
    """Limita espera BQ para não travar a Visão Geral (staff e portal)."""
    timeout = int(
        os.environ.get("MONITOR_IMPACT_BQ_TIMEOUT_SEC")
        or os.environ.get("MONITOR_PORTAL_BQ_TIMEOUT_SEC")
        or "60"
    )
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(day_exceed_for_request, request, dataset, ref_date, light=light)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "day_exceed_bq_timeout dataset=%s ref=%s sec=%s",
            dataset,
            ref_date,
            timeout,
        )
        return None, None, None, None, None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _day_exceed_cache_key(dataset: str, ref_date: str, *, alerts_source: str, light: bool) -> str:
    ref = str(ref_date)[:10]
    src = (alerts_source or "merge").strip().lower()
    lite = "lite" if light else "full"
    fp = bq_fingerprint()
    return f"day_exceed_pts_v5_{src}_{lite}_{dataset}_{ref}_{fp}"


def day_exceed_cache_peek(request, dataset: str, ref_date: str, *, light: bool | None = None):
    from monitor_web.template_paths import is_portal_request

    src = alerts_source_for_request(request)
    use_light = light
    if use_light is None:
        use_light = is_portal_request(request) and src == "merge"
    key = _day_exceed_cache_key(dataset, ref_date, alerts_source=src, light=use_light)
    return cached_peek(key)


def _raijin_alert_kms_for_day(dataset: str, ref_date: str) -> int:
    """KPI: km com pred=1 no dia (cache curto)."""
    fp = bq_fingerprint()
    key = f"raijin_alert_kms_v2_{dataset}_{str(ref_date)[:10]}_{fp}"

    def _load() -> int:
        from gcp.bq_raijin import get_raijin_day_alert_km_count

        return int(get_raijin_day_alert_km_count(dataset=dataset, ref_date=str(ref_date)[:10]) or 0)

    try:
        return int(cached_call(key, 300, _load) or 0)
    except Exception as exc:
        logger.warning("raijin_alert_kms failed: %s", exc)
        return 0


def _station_obs_for_day(dataset: str, ref_date: str, *, hours: int = 720) -> dict:
    """Pico BNDMET/ANA no acumulado selecionado, alinhado às cores do mapa."""
    from monitor_web.services.accum_window import parse_accum_hours

    h = parse_accum_hours(hours)
    fp = bq_fingerprint()
    key = f"station_obs_v5_{dataset}_{str(ref_date)[:10]}_{h}_{fp}"

    def _load() -> dict:
        day = str(ref_date)[:10]
        out: dict = {"station_obs_hours": h}
        try:
            b_acc, _, _ = _cached_station_accum("bndmet", day, h, dataset)
        except Exception:
            b_acc = {}
        try:
            a_acc, _, _ = _cached_station_accum("ana", day, h, dataset)
        except Exception:
            a_acc = {}

        if b_acc:
            cod, mm = max(b_acc.items(), key=lambda kv: float(kv[1] or 0.0))
            out["bndmet_max_24_mm"] = float(mm)
            out["bndmet_max_24_id"] = str(cod)
        if a_acc:
            cod, mm = max(a_acc.items(), key=lambda kv: float(kv[1] or 0.0))
            out["ana_max_24_mm"] = float(mm)
            out["ana_max_24_id"] = str(cod)

        # Fallback: extremos diários clima_series se horário não tiver dado.
        if out.get("bndmet_max_24_mm") is None and out.get("ana_max_24_mm") is None:
            from gcp.bq_reader_extended import station_obs_extremes_for_day_bq

            ex = station_obs_extremes_for_day_bq(day, dataset=dataset) or {}
            if ex.get("bndmet_24_mm") is not None:
                out["bndmet_max_24_mm"] = ex.get("bndmet_24_mm")
                out["bndmet_max_24_id"] = ex.get("bndmet_24_id")
            if ex.get("ana_24_mm") is not None:
                out["ana_max_24_mm"] = ex.get("ana_24_mm")
                out["ana_max_24_id"] = ex.get("ana_24_id")
        if h != parse_accum_hours(24) and out.get("bndmet_max_24_mm") is None and out.get("ana_max_24_mm") is None:
            rows = _station_rows_for_window(dataset, day, hours=h)
            if rows:
                src, cod, mm = max(rows, key=lambda r: r[2])
                if src == "bndmet":
                    out["bndmet_max_24_mm"] = mm
                    out["bndmet_max_24_id"] = cod
                elif src in ("ews", "orion"):
                    # Keep EWS peak on the weather-station card (not mislabeled BNDMET).
                    out["bndmet_max_24_mm"] = mm
                    out["bndmet_max_24_id"] = cod
                    out["bndmet_max_24_source"] = "ews"
                else:
                    out["ana_max_24_mm"] = mm
                    out["ana_max_24_id"] = cod
        return out

    try:
        return dict(cached_call(key, 300, _load) or {})
    except Exception as exc:
        logger.warning("station_obs_for_day failed: %s", exc)
        return {}


def _merge_station_accum_rows(
    *groups: list[tuple[str, str, float]],
) -> list[tuple[str, str, float]]:
    """Por (rede, código) fica o maior acumulado entre fontes (horário local vs clima_series)."""
    best: dict[tuple[str, str], tuple[str, str, float]] = {}
    for rows in groups:
        for src, cod, mm in rows or []:
            key = (str(src), str(cod))
            try:
                val = float(mm or 0.0)
            except (TypeError, ValueError):
                continue
            prev = best.get(key)
            if prev is None or val > prev[2]:
                best[key] = (key[0], key[1], val)
    return list(best.values())


def _station_rows_for_window_uncached(dataset: str, ref_date: str, *, hours: int) -> list[tuple[str, str, float]]:
    """Estações BNDMET/ANA/EWS: une horário + clima_series (sem cache)."""
    from monitor_web.services.accum_window import accum_hours_to_days

    day = str(ref_date)[:10]
    hourly_rows: list[tuple[str, str, float]] = []
    try:
        # Paraleliza as 3 redes (antes: serial; compartilham cache do mapa).
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            fut_b = pool.submit(_cached_station_accum, "bndmet", day, hours, dataset)
            fut_a = pool.submit(_cached_station_accum, "ana", day, hours, dataset)
            fut_o = pool.submit(_cached_station_accum, "orion", day, hours, dataset)
            b_acc, _, _ = fut_b.result()
            a_acc, _, _ = fut_a.result()
            o_acc, _, _ = fut_o.result()
        for cod, mm in (b_acc or {}).items():
            try:
                hourly_rows.append(("bndmet", str(cod), float(mm or 0.0)))
            except (TypeError, ValueError):
                continue
        for cod, mm in (a_acc or {}).items():
            try:
                hourly_rows.append(("ana", str(cod), float(mm or 0.0)))
            except (TypeError, ValueError):
                continue
        for cod, mm in (o_acc or {}).items():
            # Skip legacy IMEI codes — display as EWS with proper station id only.
            if "IMEI_" in str(cod).upper():
                continue
            try:
                val = float(mm or 0.0)
            except (TypeError, ValueError):
                continue
            # Physical ceiling: hour-cap × window (soft mm/dia never re-enter peaks).
            if val > float(hours) * 120.0:
                continue
            hourly_rows.append(("ews", str(cod), val))
    except Exception:
        pass

    series_rows: list[tuple[str, str, float]] = []
    try:
        from gcp.bq_reader_extended import station_window_peaks_from_clima_series_bq

        days = accum_hours_to_days(hours)
        series_rows = station_window_peaks_from_clima_series_bq(day, days=days, dataset=dataset)
        series_rows = [
            (src, cod, mm)
            for src, cod, mm in (series_rows or [])
            if "IMEI_" not in str(cod).upper() and float(mm or 0.0) <= float(hours) * 120.0
        ]
    except Exception:
        series_rows = []

    return _merge_station_accum_rows(hourly_rows, series_rows)


def _station_rows_for_window(dataset: str, ref_date: str, *, hours: int) -> list[tuple[str, str, float]]:
    """Linhas por janela com cache Redis/LocMem (evita N×BQ no painel + entorno)."""
    day = str(ref_date)[:10]
    h = int(hours)
    fp = bq_fingerprint()
    key = f"station_rows_v1_{dataset}_{day}_{h}_{fp}"
    return list(cached_call(key, STATION_ROWS_TTL, _station_rows_for_window_uncached, dataset, day, hours=h) or [])


# Raio ao redor do km selecionado; buffer do corredor EFC quando não há foco.
ENTORNO_FOCUS_RADIUS_KM = float(os.environ.get("MONITOR_ENTORNO_FOCUS_KM", "45") or "45")
ENTORNO_CORRIDOR_BUFFER_KM = float(os.environ.get("MONITOR_ENTORNO_CORRIDOR_KM", "40") or "40")


def _closest_km_and_dist_on_line(points, cumulative_km, lat_q, lon_q):
    """Retorna (km_ao_longo, dist_km) do ponto à projeção na ferrovia."""
    if points is None or cumulative_km is None or len(points) < 2 or len(cumulative_km) < 2:
        return None, None
    from lib_efc import _haversine_km

    pts = np.asarray(points, dtype=float)
    cum = np.asarray(cumulative_km, dtype=float)
    best_km, best_d = None, float("inf")
    for i in range(len(pts) - 1):
        lat_a, lon_a = float(pts[i, 0]), float(pts[i, 1])
        lat_b, lon_b = float(pts[i + 1, 0]), float(pts[i + 1, 1])
        dlat, dlon = lat_b - lat_a, (lon_b - lon_a) * np.cos(np.radians(lat_a))
        denom = dlat * dlat + dlon * dlon + 1e-20
        t = ((lat_q - lat_a) * dlat + (lon_q - lon_a) * dlon) / denom
        t = float(np.clip(t, 0.0, 1.0))
        lat_proj = lat_a + t * (lat_b - lat_a)
        lon_proj = lon_a + t * (lon_b - lon_a)
        d = float(_haversine_km(lat_q, lon_q, lat_proj, lon_proj))
        if d < best_d:
            best_d = d
            best_km = float(cum[i] + t * (cum[i + 1] - cum[i]))
    if best_km is None:
        return None, None
    return best_km, best_d


def _station_geo_index(dataset: str) -> dict[str, dict]:
    """Índice por código de estação com lat/lon/nome/município (BNDMET+ANA)."""
    from monitor_web.services.home_impact_enrich import (
        _build_station_lookup,
        _cached_lookups,
        _code_variants,
    )

    try:
        tables = _cached_lookups(dataset)
    except Exception:
        try:
            from lib_efc import load_ana_estacoes, load_bndmet_estacoes, load_orion_estacoes

            tables = {
                "bndmet": _build_station_lookup(load_bndmet_estacoes(bq_dataset=dataset)),
                "ana": _build_station_lookup(load_ana_estacoes(bq_dataset=dataset)),
                "ews": _build_station_lookup(load_orion_estacoes(bq_dataset=dataset)),
            }
        except Exception:
            return {}
    out: dict[str, dict] = {}
    for net, table in (
        ("bndmet", tables.get("bndmet") or {}),
        ("ana", tables.get("ana") or {}),
        ("ews", tables.get("ews") or {}),
    ):
        for _key, ent in table.items():
            if not isinstance(ent, dict):
                continue
            cod = str(ent.get("code") or "").strip()
            if not cod:
                continue
            lat, lon = ent.get("lat"), ent.get("lon")
            if lat is None or lon is None or not pd.notna(lat) or not pd.notna(lon):
                continue
            payload = {
                "code": cod,
                "name": str(ent.get("name") or cod).strip(),
                "lat": float(lat),
                "lon": float(lon),
                "municipio": (str(ent.get("municipio") or "").strip() or None),
                "km_efc": ent.get("km_efc"),
                "network": net,
            }
            for variant in _code_variants(cod):
                # Preferir entrada da rede nativa quando houver colisão.
                prev = out.get(variant)
                if prev is None or prev.get("network") == net:
                    out[variant] = payload
    return out


def _entorno_empty_peak() -> dict:
    return {
        "mm": 0.0,
        "station_id": None,
        "source": None,
        "dist_km": None,
        "municipio": None,
        "station_name": None,
        "km_efc": None,
        "n_in_radius": 0,
    }


def _station_entorno_peaks_for_windows(
    *,
    dataset: str,
    ref_date: str,
    windows: tuple,
    focus_km: int | None,
    main_line,
    cum_km,
    has_km_focus: bool,
    rows_by_hours: dict[int, list] | None = None,
) -> dict:
    """
    Máximo observado entre estações no entorno do km (raio) ou do corredor EFC.

    - Com km selecionado: raio ENTORNO_FOCUS_RADIUS_KM ao redor do ponto na ferrovia.
    - Sem km: estações a até ENTORNO_CORRIDOR_BUFFER_KM de qualquer ponto da linha.
    - ``rows_by_hours`` evita reconsultar BQ (reusa o mesmo fetch do painel).
    """
    from lib_efc import haversine_km, position_at_km

    day = str(ref_date)[:10]
    geo = _station_geo_index(dataset)
    focus_pos = None
    mode = "corridor"
    radius = float(ENTORNO_CORRIDOR_BUFFER_KM)
    if has_km_focus and focus_km is not None and main_line is not None and cum_km is not None:
        focus_pos = position_at_km(float(focus_km), main_line, cum_km)
        if focus_pos is not None:
            mode = "km"
            radius = float(ENTORNO_FOCUS_RADIUS_KM)

    peaks: dict = {}
    for kind, hours, _th in windows:
        if rows_by_hours is not None and int(hours) in rows_by_hours:
            rows = rows_by_hours[int(hours)]
        else:
            rows = _station_rows_for_window(dataset, day, hours=hours)
        best = None
        n_in = 0
        for src, cod, mm in rows or []:
            try:
                mm_f = float(mm or 0.0)
            except (TypeError, ValueError):
                continue
            info = geo.get(str(cod))
            if info is None:
                from monitor_web.services.home_impact_enrich import _code_variants

                for key in _code_variants(str(cod)):
                    info = geo.get(key)
                    if info is not None:
                        break
            if not info:
                continue
            lat, lon = info["lat"], info["lon"]
            if mode == "km" and focus_pos is not None:
                dist = float(haversine_km(lat, lon, float(focus_pos[0]), float(focus_pos[1])))
            else:
                _km_along, dist = _closest_km_and_dist_on_line(main_line, cum_km, lat, lon)
                if dist is None:
                    continue
                dist = float(dist)
            if dist > radius:
                continue
            n_in += 1
            cand = (mm_f, -dist, src, cod, dist, info)
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
                best = cand
        if best is None:
            peaks[kind] = _entorno_empty_peak()
            peaks[kind]["n_in_radius"] = int(n_in)
            continue
        mm_f, _neg_d, src, cod, dist, info = best
        peaks[kind] = {
            "mm": float(mm_f),
            "station_id": str(cod),
            "source": str(src),
            "dist_km": round(float(dist), 1),
            "municipio": info.get("municipio"),
            "station_name": info.get("name"),
            "km_efc": info.get("km_efc"),
            "n_in_radius": int(n_in),
        }
    return {
        "station_panel_entorno_peaks": peaks,
        "entorno_mode": mode,
        "entorno_radius_km": radius,
        "entorno_focus_km": int(focus_km) if (mode == "km" and focus_km is not None) else None,
    }


def _station_panel_windows(
    dataset: str,
    ref_date: str,
    *,
    focus_km: int | None = None,
    main_line=None,
    cum_km=None,
    has_km_focus: bool = False,
) -> dict:
    """
    Picos e contagens BNDMET/ANA nas 4 janelas do menu Acumulado.

    Usado no painel direito (exceto Raijin) — só observação, sem MERGE/modelo.
    Também calcula o máximo no entorno do km / corredor EFC.
    """
    from lib_efc import (
        THRESHOLD_24H_MM,
        THRESHOLD_30D_MM,
        THRESHOLD_7D_MM,
        THRESHOLD_120H_MM,
    )
    from monitor_web.services.accum_window import (
        ACCUM_HOURS_24,
        ACCUM_HOURS_5D,
        ACCUM_HOURS_7D,
        ACCUM_HOURS_30D,
    )

    day = str(ref_date)[:10]
    fp = bq_fingerprint()
    focus_tag = f"km{int(focus_km)}" if has_km_focus and focus_km is not None else "corridor"
    key = f"station_panel_v5_{dataset}_{day}_{focus_tag}_{fp}"
    windows = (
        ("24h", ACCUM_HOURS_24, float(THRESHOLD_24H_MM)),
        ("5d", ACCUM_HOURS_5D, float(THRESHOLD_120H_MM)),
        ("7d", ACCUM_HOURS_7D, float(THRESHOLD_7D_MM)),
        ("30d", ACCUM_HOURS_30D, float(THRESHOLD_30D_MM)),
    )

    def _load() -> dict:
        # 4 janelas em paralelo (antes: serial × entorno = ~32 BQ).
        rows_by_hours: dict[int, list] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = {
                int(hours): pool.submit(_station_rows_for_window, dataset, day, hours=int(hours))
                for _kind, hours, _th in windows
            }
            for h, fut in futs.items():
                try:
                    rows_by_hours[h] = list(fut.result() or [])
                except Exception as exc:
                    logger.warning("station_rows window %sh failed: %s", h, exc)
                    rows_by_hours[h] = []

        peaks: dict = {}
        for kind, hours, th in windows:
            rows = rows_by_hours.get(int(hours)) or []
            if not rows:
                peaks[kind] = {
                    "mm": 0.0,
                    "station_id": None,
                    "source": None,
                    "n_above": 0,
                    "n_total": 0,
                }
                continue
            src, cod, mm = max(rows, key=lambda r: r[2])
            n_total = len(rows)
            n_above = sum(1 for _, _, v in rows if v > th)
            peaks[kind] = {
                "mm": float(mm),
                "station_id": cod,
                "source": src,
                "n_above": int(n_above),
                "n_total": int(n_total),
            }
        out = {"station_panel_peaks": peaks}
        try:
            out.update(
                _station_entorno_peaks_for_windows(
                    dataset=dataset,
                    ref_date=day,
                    windows=windows,
                    focus_km=focus_km,
                    main_line=main_line,
                    cum_km=cum_km,
                    has_km_focus=has_km_focus,
                    rows_by_hours=rows_by_hours,
                )
            )
        except Exception as exc:
            logger.warning("station_entorno_peaks failed: %s", exc)
            out["station_panel_entorno_peaks"] = {}
            out["entorno_mode"] = "corridor"
            out["entorno_radius_km"] = float(ENTORNO_CORRIDOR_BUFFER_KM)
            out["entorno_focus_km"] = None
        return out

    try:
        return dict(
            cached_call_swr(key, STATION_PANEL_TTL, _load, stale_ttl=STATION_PANEL_TTL * 4) or {}
        )
    except Exception as exc:
        logger.warning("station_panel_windows failed: %s", exc)
        return {"station_panel_peaks": {}, "station_panel_entorno_peaks": {}}


def _attach_station_obs(
    impact: dict,
    *,
    dataset: str,
    ref_date: str,
    lang_pt: bool,
    accum_hours: int = 720,
    focus_km: int | None = None,
    main_line=None,
    cum_km=None,
    has_km_focus: bool = False,
) -> dict:
    """Anexa picos de estação (janela ativa + 24/5/7/30) para o painel."""
    from monitor_web.services.accum_window import parse_accum_hours

    if not impact or not impact.get("available"):
        return impact
    out = dict(impact)
    hours = parse_accum_hours(accum_hours)
    obs = _station_obs_for_day(dataset, ref_date, hours=hours)
    for k, v in obs.items():
        if v is not None:
            out[k] = v
    out["station_obs_hours"] = hours
    panel = _station_panel_windows(
        dataset,
        ref_date,
        focus_km=focus_km,
        main_line=main_line,
        cum_km=cum_km,
        has_km_focus=has_km_focus,
    )
    out.update(panel)
    try:
        from monitor_web.services.home_impact_enrich import enrich_home_impact

        out = enrich_home_impact(
            out, lang_pt=lang_pt, main_line=main_line, cum_km=cum_km, bq_dataset=dataset
        )
    except Exception as exc:
        logger.warning("enrich station obs failed: %s", exc)
    return out


def _raijin_tp_day_df(dataset: str, ref_date: str):
    """DF de TP do dia (cache do hero/tabela; senão carrega a mesma query)."""
    from monitor_web.services.suscetibilidade import filter_raijin_tp_df

    sel = str(ref_date)[:10]
    cached = cached_peek(
        _day_exceed_cache_key(dataset, sel, alerts_source="raijin", light=False)
    ) or cached_peek(
        _day_exceed_cache_key(dataset, sel, alerts_source="raijin", light=True)
    )
    df = cached[0] if cached is not None else None
    if df is None:
        try:
            tup = _day_exceed_and_points(dataset, sel, alerts_source="raijin", light=False)
            df = tup[0] if tup is not None else None
        except Exception as exc:
            logger.warning("raijin_tp_day_df failed: %s", exc)
            df = None
    return filter_raijin_tp_df(df)


def _align_indicators_impact(
    impact: dict,
    *,
    request,
    dataset: str,
    sel: str,
    lang_pt: bool,
    km_max: int,
    accum_hours: int,
    focus_km: int | None = None,
    main_line=None,
    cum_km=None,
    has_km_focus: bool = False,
    portal_fast: bool = False,
) -> tuple[dict, int]:
    """Mesmo período do mapa + TPs do dia (hero/tabela) nos envelopes do painel.

    ``portal_fast=True``: evita BQ bloqueante (Raijin envelopes / backfill peaks /
    station panel miss). Serve cache se houver; caso contrário zera e deixa o
    warmup / próximo request preencher.
    """
    from monitor_web.services.home_accumulated_alerts import (
        apply_raijin_period_envelopes,
        parse_accumulated_period,
        period_label_pt,
    )
    from monitor_web.services.impact_lite import (
        backfill_impact_peaks,
        coerce_missing_peaks_to_zero,
        impact_lite_from_day_exceed,
    )
    from monitor_web.services.suscetibilidade import count_unique_alert_kms
    from monitor_web.template_paths import is_portal_request

    d_start, d_end, period_key = parse_accumulated_period(request, date_end_cap=sel)
    period_lbl = period_label_pt(period_key, d_start, d_end)
    out = dict(impact or {})
    out["available"] = True
    out["period_label_pt"] = period_lbl
    out["period_label"] = period_lbl
    out["ref_date"] = sel

    src = alerts_source_for_request(request) if request is not None else "merge"
    use_portal_fast = bool(portal_fast) or (
        request is not None and is_portal_request(request) and src == "merge"
    )

    # Portal MERGE: não bloqueia em queries Raijin (KPIs são limiares MERGE).
    n_tp_day = 0
    if not use_portal_fast or src == "raijin":
        tp_day = _raijin_tp_day_df(dataset, sel)
        n_tp_day = count_unique_alert_kms(tp_day)
        if tp_day is not None and not tp_day.empty:
            tp_lite = impact_lite_from_day_exceed(
                tp_day,
                km_max=km_max,
                lang_pt=lang_pt,
                period_label=period_lbl,
                ref_date=sel,
            )
            for k in (
                "max24_ensemble_mm",
                "max24_ensemble_km",
                "n_km_24_envelope",
                "pct_km_24_envelope",
            ):
                if tp_lite.get(k) is not None:
                    out[k] = tp_lite[k]
        out = apply_raijin_period_envelopes(
            out,
            dataset=dataset,
            date_start=d_start,
            date_end=d_end,
            period_key=period_key,
            km_max=km_max,
            n_km_24=n_tp_day if n_tp_day else None,
        )
        if not use_portal_fast:
            out = backfill_impact_peaks(out, dataset=dataset, ref_date=sel)
    out = coerce_missing_peaks_to_zero(out)

    if use_portal_fast:
        # Peek cache do painel de estações; se frio, segue sem bloquear first paint.
        fp = bq_fingerprint()
        focus_tag = f"km{int(focus_km)}" if has_km_focus and focus_km is not None else "corridor"
        panel_key = f"station_panel_v5_{dataset}_{str(sel)[:10]}_{focus_tag}_{fp}"
        panel = cached_peek(panel_key)
        if panel:
            out = dict(out)
            out.update(panel)
            try:
                from monitor_web.services.home_impact_enrich import enrich_home_impact

                out = enrich_home_impact(
                    out, lang_pt=lang_pt, main_line=main_line, cum_km=cum_km, bq_dataset=dataset
                )
            except Exception:
                pass
        else:
            # Dispara warm em background (não espera).
            def _warm_panel():
                try:
                    _station_panel_windows(
                        dataset,
                        sel,
                        focus_km=focus_km,
                        main_line=main_line,
                        cum_km=cum_km,
                        has_km_focus=has_km_focus,
                    )
                except Exception as exc:
                    logger.warning("station_panel warm failed: %s", exc)

            try:
                import threading

                threading.Thread(target=_warm_panel, name="warm-station-panel", daemon=True).start()
            except Exception:
                pass
    else:
        out = _attach_station_obs(
            out,
            dataset=dataset,
            ref_date=sel,
            lang_pt=lang_pt,
            accum_hours=accum_hours,
            focus_km=focus_km,
            main_line=main_line,
            cum_km=cum_km,
            has_km_focus=has_km_focus,
        )
    n_raijin = int(out.get("n_km_24_envelope") or 0)
    return out, n_raijin


def _log_timing(stage: str, ms: float, **extra) -> None:
    logger.info("home_timing", extra={"stage": stage, "ms": round(ms, 1), **extra})


def build_status_fragment_portal_fast(request) -> dict:
    """Portal: resposta imediata — usa cache BQ ou mensagem para atualizar."""
    import time

    t0 = time.perf_counter()
    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen, _, _ = _clamp_date_to_bq_range(p["chosen"], dataset)
    sel = chosen.isoformat()
    use_bq = bq_locations_probe(dataset)
    ctx = {
        "lang_pt": lang_pt,
        "use_bq": use_bq,
        "selected_date": sel,
        "selected_display": chosen.strftime("%d/%m/%Y"),
        "status_normal": True,
        "n_crit_day": 0,
        "alert_cards_html": "",
        "exceed_table": [],
        "bq_warning": None,
    }
    if not use_bq:
        from monitor_web.services.bq_status import bq_warning_message

        ctx["bq_warning"] = bq_warning_message(lang_pt=lang_pt, probe_failed=True)
        ctx.update(
            status_hero_context(
                lang_pt=lang_pt,
                selected_display=ctx["selected_display"],
                status_normal=True,
                n_crit_day=0,
                df_day=None,
            )
        )
        return ctx

    cached = day_exceed_cache_peek(request, dataset, sel)
    ctx["portal_cache_ready"] = cached is not None
    if cached is None:
        ctx.update(
            status_hero_context(
                lang_pt=lang_pt,
                selected_display=ctx["selected_display"],
                status_normal=True,
                n_crit_day=0,
                df_day=None,
            )
        )
        ctx["alert_cards_html"] = (
            '<span class="efc-muted-chip">'
            + ("Sem alerta no momento" if lang_pt else "No alerts right now")
            + "</span>"
        )
        _log_timing("status_portal_fast", (time.perf_counter() - t0) * 1000, cache=0)
        return ctx

    df_day, _, _, _, _ = cached
    ctx["portal_cache_ready"] = True
    from monitor_web.services.suscetibilidade import (
        build_alert_cards_html,
        cached_rail_segment_lookup,
        count_unique_alert_kms,
    )

    n_crit = count_unique_alert_kms(df_day)
    ctx["n_crit_day"] = n_crit
    ctx["status_normal"] = n_crit == 0
    src = alerts_source_for_request(request)
    ctx["alerts_source"] = src
    ctx["alerts_panel_title"] = alerts_panel_heading(src, lang_pt=lang_pt, n_kms=n_crit)
    ctx["exceed_table_title"] = exceed_table_heading(
        src, lang_pt=lang_pt, selected_display=ctx["selected_display"]
    )
    ctx.update(
        status_hero_context(
            lang_pt=lang_pt,
            selected_display=chosen.strftime("%d/%m/%Y"),
            status_normal=ctx["status_normal"],
            n_crit_day=n_crit,
            df_day=df_day,
            alerts_source=src,
        )
    )
    try:
        segment_lookup = cached_rail_segment_lookup(dataset)
    except Exception:
        segment_lookup = []
    ctx["alert_cards_html"] = build_alert_cards_html(
        df_day, lang_pt=lang_pt, lookup=segment_lookup, sel_fallback=sel
    )
    ctx["exceed_table"] = build_exceed_table_for_status(
        df_day=df_day,
        dataset=dataset,
        ref_date=sel,
        selected_display=ctx["selected_display"],
        alerts_source=src,
    )
    _log_timing("status_portal_fast", (time.perf_counter() - t0) * 1000, cache=1)
    return ctx


def build_status_fragment(request) -> dict:
    import time

    t0 = time.perf_counter()
    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen, bq_lo, bq_hi = _clamp_date_to_bq_range(p["chosen"], dataset)
    sel = chosen.isoformat()
    use_bq = bq_locations_probe(dataset)

    ctx = {
        "lang_pt": lang_pt,
        "use_bq": use_bq,
        "selected_date": sel,
        "selected_display": chosen.strftime("%d/%m/%Y"),
        "status_normal": True,
        "n_crit_day": 0,
        "alert_cards_html": "",
        "exceed_table": [],
        "bq_warning": None,
    }
    if not use_bq:
        from monitor_web.services.bq_status import bq_warning_message

        ctx["bq_warning"] = bq_warning_message(lang_pt=lang_pt, probe_failed=True)
        ctx.update(
            status_hero_context(
                lang_pt=lang_pt,
                selected_display=ctx["selected_display"],
                status_normal=True,
                n_crit_day=0,
                df_day=None,
            )
        )
        _log_timing("status", (time.perf_counter() - t0) * 1000, bq_jobs=0)
        return ctx

    from monitor_web.template_paths import is_portal_request

    df_day, _, _, _, _ = day_exceed_for_request_safe(request, dataset, sel)
    if df_day is None and is_portal_request(request):
        ctx.update(
            status_hero_context(
                lang_pt=lang_pt,
                selected_display=chosen.strftime("%d/%m/%Y"),
                status_normal=True,
                n_crit_day=0,
                df_day=None,
            )
        )
        ctx["alert_cards_html"] = (
            '<span class="efc-muted-chip">'
            + ("Aguardando dados…" if lang_pt else "Waiting for data…")
            + "</span>"
        )
        _log_timing("status", (time.perf_counter() - t0) * 1000, bq_jobs=0, timeout=1)
        return ctx
    from monitor_web.services.suscetibilidade import (
        build_alert_cards_html,
        cached_rail_segment_lookup,
        count_unique_alert_kms,
    )

    n_crit = count_unique_alert_kms(df_day)
    ctx["n_crit_day"] = n_crit
    ctx["status_normal"] = n_crit == 0
    src = alerts_source_for_request(request)
    ctx["alerts_source"] = src
    ctx["alerts_panel_title"] = alerts_panel_heading(src, lang_pt=lang_pt, n_kms=n_crit)
    ctx["exceed_table_title"] = exceed_table_heading(
        src, lang_pt=lang_pt, selected_display=ctx["selected_display"]
    )
    ctx.update(
        status_hero_context(
            lang_pt=lang_pt,
            selected_display=chosen.strftime("%d/%m/%Y"),
            status_normal=ctx["status_normal"],
            n_crit_day=n_crit,
            df_day=df_day,
            alerts_source=src,
        )
    )

    try:
        segment_lookup = cached_rail_segment_lookup(dataset)
    except Exception as exc:
        logger.warning("rail_segment_lookup failed: %s", exc)
        segment_lookup = []
    ctx["alert_cards_html"] = build_alert_cards_html(
        df_day, lang_pt=lang_pt, lookup=segment_lookup, sel_fallback=sel
    )
    ctx["exceed_table"] = build_exceed_table_for_status(
        df_day=df_day,
        dataset=dataset,
        ref_date=sel,
        selected_display=ctx["selected_display"],
        alerts_source=src,
    )
    _log_timing("status", (time.perf_counter() - t0) * 1000, bq_jobs=1)
    return ctx


def build_indicators_fragment_portal_fast(request) -> dict:
    """Indicadores sem BQ bloqueante: cache → lite de day_exceed → skeleton."""
    import time

    from monitor_web.services.home_accumulated_alerts import parse_accumulated_period, period_label_pt
    from monitor_web.services.impact_lite import impact_lite_from_day_exceed

    t0 = time.perf_counter()
    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen, _, _ = _clamp_date_to_bq_range(p["chosen"], dataset)
    sel = chosen.isoformat()
    ctx = {
        "lang_pt": lang_pt,
        "use_bq": True,
        "indicators_ctx": {"available": False},
        "kpi_html": "",
        "panel_indicators": "",
    }

    d_start, d_end, period_key = parse_accumulated_period(request, date_end_cap=sel)
    period_lbl = period_label_pt(period_key, d_start, d_end)
    fp = bq_fingerprint()
    impact_day_key = f"impact_day_{dataset}_{sel}_{fp}"
    impact_legacy_key = f"impact_{dataset}_{sel}_{period_key}_{d_start}_{d_end}_{fp}"
    impact = cached_peek(impact_day_key) or cached_peek(impact_legacy_key)
    # Preferir excedência operacional MERGE (inclui 7d/30d). light só cobre 24h/5d do dia.
    # Não usar alerts_source=raijin aqui — KPIs do painel são limiares MERGE.
    cached = cached_peek(_day_exceed_cache_key(dataset, sel, alerts_source="merge", light=False))
    if cached is None:
        cached = cached_peek(_day_exceed_cache_key(dataset, sel, alerts_source="merge", light=True))

    if impact is None or not impact.get("available"):
        df_day = cached[0] if cached is not None else None
        # Portal fast: NÃO dispara BQ bloqueante no first paint.
        # Se o cache day_exceed estiver frio, devolve lite vazio e aquece em background.
        if df_day is None or (hasattr(df_day, "empty") and df_day.empty):

            def _warm_day_exceed():
                try:
                    _day_exceed_and_points(dataset, sel, alerts_source="merge", light=False)
                    _day_merge_light_impl(dataset, sel)
                except Exception as exc:
                    logger.warning("indicators day_exceed warm failed: %s", exc)

            try:
                import threading

                threading.Thread(
                    target=_warm_day_exceed, name="warm-day-exceed", daemon=True
                ).start()
            except Exception:
                pass
            df_day = None
        impact = impact_lite_from_day_exceed(
            df_day,
            km_max=p["km_max"],
            lang_pt=lang_pt,
            period_label=period_lbl,
            ref_date=sel,
        )

    impact, n_raijin = _align_indicators_impact(
        impact,
        request=request,
        dataset=dataset,
        sel=sel,
        lang_pt=lang_pt,
        km_max=p["km_max"],
        accum_hours=p["accum_hours"],
        focus_km=p.get("fc_km"),
        main_line=p.get("main_line"),
        cum_km=p.get("cum_km"),
        has_km_focus=bool(p.get("has_km_focus")),
        portal_fast=True,
    )

    df_day_ind = cached[0] if cached is not None else None
    from monitor_web.services.home_indicators_context import build_indicators_context
    from monitor_web.services.suscetibilidade import (
        cached_rail_segment_lookup,
        exceed_susc_stats_by_kind,
    )

    try:
        segment_lookup = cached_rail_segment_lookup(dataset)
    except Exception:
        segment_lookup = []
    indicators_ctx = build_indicators_context(
        lang_pt=lang_pt,
        impact=impact,
        th24=THRESHOLD_24H_MM,
        th120=THRESHOLD_120H_MM,
        th7=THRESHOLD_7D_MM,
        th30=THRESHOLD_30D_MM,
        segment_lookup=segment_lookup,
        exceed_susc_stats=exceed_susc_stats_by_kind(df_day_ind, segment_lookup),
        km_max=p["km_max"],
        raijin_alert_kms=n_raijin,
        accum_hours=p["accum_hours"],
        bq_dataset=dataset,
    )
    from monitor_web.services.efc_ia_insight import build_ia_insight_context, goes_manifest_summary

    indicators_ctx["ia_insight"] = build_ia_insight_context(
        lang_pt=lang_pt,
        impact=impact,
        goes=goes_manifest_summary(),
        threshold_24=THRESHOLD_24H_MM,
    )
    ctx["indicators_ctx"] = indicators_ctx
    _log_timing("indicators_portal_fast", (time.perf_counter() - t0) * 1000, cache=1 if not impact.get("lite") else 0)
    return ctx


def build_indicators_fragment(request) -> dict:
    import time

    t0 = time.perf_counter()
    # Sempre responde com lite/cache; ?force=1 tenta enrich BQ do dia.
    fast = build_indicators_fragment_portal_fast(request)
    force = (request.GET.get("force") or "").strip().lower() in ("1", "true", "yes")
    if force:
        _log_timing("indicators", (time.perf_counter() - t0) * 1000, cache=0, bq_jobs=1)
        return build_indicators_fragment_blocking(request)
    _log_timing("indicators", (time.perf_counter() - t0) * 1000, cache=1, bq_jobs=0)
    return fast


def build_indicators_fragment_blocking(request) -> dict:
    """Carrega indicadores com timeout BQ (uso em ?force=1 / warmup)."""
    import time

    t0 = time.perf_counter()
    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen, _, _ = _clamp_date_to_bq_range(p["chosen"], dataset)
    sel = chosen.isoformat()

    ctx = {
        "lang_pt": lang_pt,
        "use_bq": bq_locations_probe(dataset),
        "indicators_ctx": {"available": False},
        "kpi_html": "",
        "panel_indicators": "",
    }
    if not ctx["use_bq"]:
        _log_timing("indicators", (time.perf_counter() - t0) * 1000, bq_jobs=0)
        return ctx

    from monitor_web.services.home_impact_enrich import home_impact_for_request_safe

    impact = home_impact_for_request_safe(
        request,
        dataset=dataset,
        sel=sel,
        lang_pt=lang_pt,
        main_line=p["main_line"],
        cum_km=p["cum_km"],
        ttl=_ttl(),
    )
    impact, n_raijin = _align_indicators_impact(
        impact,
        request=request,
        dataset=dataset,
        sel=sel,
        lang_pt=lang_pt,
        km_max=p["km_max"],
        accum_hours=p["accum_hours"],
        focus_km=p.get("fc_km"),
        main_line=p.get("main_line"),
        cum_km=p.get("cum_km"),
        has_km_focus=bool(p.get("has_km_focus")),
    )
    from monitor_web.services.home_indicators_context import build_indicators_context

    from monitor_web.services.suscetibilidade import (
        cached_rail_segment_lookup,
        exceed_susc_stats_by_kind,
    )

    segment_lookup = cached_rail_segment_lookup(dataset)
    _, df_day_ind, _, _, _ = day_exceed_for_request_safe(request, dataset, sel)
    indicators_ctx = build_indicators_context(
        lang_pt=lang_pt,
        impact=impact,
        th24=THRESHOLD_24H_MM,
        th120=THRESHOLD_120H_MM,
        th7=THRESHOLD_7D_MM,
        th30=THRESHOLD_30D_MM,
        segment_lookup=segment_lookup,
        exceed_susc_stats=exceed_susc_stats_by_kind(df_day_ind, segment_lookup),
        km_max=p["km_max"],
        raijin_alert_kms=n_raijin,
        accum_hours=p["accum_hours"],
        bq_dataset=dataset,
    )
    from monitor_web.services.efc_ia_insight import build_ia_insight_context, goes_manifest_summary

    indicators_ctx["ia_insight"] = build_ia_insight_context(
        lang_pt=lang_pt,
        impact=impact,
        goes=goes_manifest_summary(),
        threshold_24=THRESHOLD_24H_MM,
    )
    ctx["indicators_ctx"] = indicators_ctx

    if not impact.get("available"):
        from gcp.bq_reader_clima import count_precip_24h_cells_above_for_date

        bq_tuple = day_exceed_for_request_safe(request, dataset, sel)
        _, _, bq_series, bq_locs, _ = bq_tuple
        if bq_series is not None:
            n24 = count_precip_24h_cells_above_for_date(bq_series, sel, THRESHOLD_24H_MM)
            total = len(bq_locs) if bq_locs is not None else 0
            pct24 = 100.0 * n24 / total if total else 0
            stats_local = get_merge_along_railway_stats(ref_date=sel)
            ctx["kpi_html"] = (
                kpi_card(
                    f"{pct24:.0f}%",
                    "24 h > 49 mm",
                    f"{n24} cél.",
                    color_slot="warning" if pct24 else "normal",
                    compact=True,
                )
                + kpi_card(
                    f"{(stats_local or {}).get('max24_val') or '—'}",
                    "MERGE 24h",
                    f"km {(stats_local or {}).get('max24_km') or '—'}",
                    compact=True,
                )
            )

    _log_timing("indicators", (time.perf_counter() - t0) * 1000, bq_jobs=1)
    return ctx


def parse_susc_show(request) -> bool:
    """Preferência da camada Suscetibilidade (?susc=1). Default OFF."""
    if request is None:
        return False
    raw = (request.GET.get("susc") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _map_html_for_day_uncached(
    sel: str,
    dataset: str,
    accum_hours: int,
    lang_pt: bool,
    *,
    embed_document: bool = False,
    request=None,
) -> tuple[str, str | None]:
    from monitor_web.services.map_alert_markers import (
        alert_markers_from_df,
        operational_day_alert_markers_from_df,
    )

    show_susc = parse_susc_show(request)
    cache_tag = ("embed" if embed_document else "inline") + ("_susc1" if show_susc else "_susc0")
    stable_key = map_folium_stable_cache_key(
        ver=MAP_CACHE_VER,
        sel=sel,
        dataset=dataset,
        accum_hours=accum_hours,
        km_step=MAP_KM_STEP,
        cache_tag=cache_tag,
    )
    # Peek rápido: evita BQ day_exceed quando o HTML Folium já está em Redis/GCS.
    map_html = cache.get(stable_key)
    if not map_html:
        try:
            from monitor_web.services.map_gcs_cache import get_map_html_gcs

            map_html = get_map_html_gcs(stable_key)
            if map_html:
                cache.set(stable_key, map_html, MAP_CACHE_TTL)
        except Exception:
            map_html = None
    if map_html:
        return map_html, None

    from monitor_web.template_paths import is_portal_request

    # Portal: path leve (24h/5d / daily_exceed) — mapa não precisa da janela 30d.
    use_light = bool(request and is_portal_request(request))
    src = alerts_source_for_request(request) if request else "merge"

    def _load_exceed():
        if request:
            return day_exceed_for_request(request, dataset, sel, light=use_light)
        return _day_exceed_and_points(dataset, sel, alerts_source="merge", light=use_light)

    # Prefetch catálogos em paralelo com day_exceed (aquece Redis para o build Folium).
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fut_ex = pool.submit(_load_exceed)
        f_b = pool.submit(_cached_station_catalog, "bndmet", dataset)
        f_a = pool.submit(_cached_station_catalog, "ana", dataset)
        f_o = pool.submit(_cached_station_catalog, "orion", dataset)
        df_day, _, _, _, pts = fut_ex.result()
        for f in (f_b, f_a, f_o):
            try:
                f.result()
            except Exception:
                pass

    if pts is None:
        return "", _map_unavailable_message(lang_pt, hint="sem localizações")
    pts_24, pts_120 = pts
    # Mesma fonte do painel lateral (Raijin ou MERGE) — evita contagem divergente.
    if src == "raijin":
        alert_mk = alert_markers_from_df(df_day, raijin_only=True, tp_only=True)
        # Com fonte Raijin: só TP no mapa (sem FP/pending e sem fallback MERGE).
        pts_24, pts_120 = (), ()
    else:
        # Reusa o mesmo day_exceed (já light/merge) — evita 2º scan BQ full.
        alert_mk = operational_day_alert_markers_from_df(df_day)
    main_line, _, _, _ = _railway()
    if main_line is None:
        return "", _map_unavailable_message(
            lang_pt, hint="geometria da ferrovia indisponível" if lang_pt else "railway geometry unavailable"
        )
    try:
        map_key = map_folium_cache_key(
            ver=MAP_CACHE_VER,
            sel=sel,
            dataset=dataset,
            accum_hours=accum_hours,
            km_step=MAP_KM_STEP,
            cache_tag=cache_tag,
            alert_mk=alert_mk,
            csv_mtimes=tuple(
                _csv_mtime(p)
                for p in (BNDMET_ESTACOES_CSV, BNDMET_DADOS_CSV, ANA_ESTACOES_CSV, ANA_ADOTADA_CSV)
            ),
        )
        map_html = cache.get(map_key)
        if not map_html:
            from monitor_web.services.map_gcs_cache import get_map_html_gcs

            map_html = get_map_html_gcs(map_key)
            if map_html:
                cache.set(map_key, map_html, MAP_CACHE_TTL)
                cache.set(stable_key, map_html, MAP_CACHE_TTL)
                try:
                    from monitor_web.services.map_gcs_cache import put_map_html_gcs

                    put_map_html_gcs(stable_key, map_html)
                except Exception:
                    pass
        if not map_html:
            map_html = _operational_map_html(
                sel,
                dataset,
                pts_24,
                pts_120,
                accum_hours,
                MAP_KM_STEP,
                embed_document,
                alert_mk,
                lang_pt,
                show_susc,
            )
            if map_html:
                cache.set(map_key, map_html, MAP_CACHE_TTL)
                cache.set(stable_key, map_html, MAP_CACHE_TTL)
                try:
                    from monitor_web.services.map_gcs_cache import put_map_html_gcs

                    put_map_html_gcs(map_key, map_html)
                    put_map_html_gcs(stable_key, map_html)
                except Exception:
                    pass
        if map_html:
            return map_html, None
        try:
            import folium  # noqa: F401
        except ImportError:
            return "", _map_unavailable_message(lang_pt, folium_missing=True)
        return "", _map_unavailable_message(lang_pt, hint="build_folium_railway_map retornou vazio")
    except Exception as exc:
        return "", _map_unavailable_message(lang_pt, hint=str(exc))


def _map_html_for_day(
    sel: str,
    dataset: str,
    accum_hours: int,
    lang_pt: bool,
    *,
    embed_document: bool = False,
    request=None,
) -> tuple[str, str | None]:
    """Gera HTML Folium com limite de tempo para não bloquear o iframe indefinidamente."""
    timeout = MAP_BUILD_TIMEOUT_SEC
    if timeout <= 0:
        return _map_html_for_day_uncached(
            sel, dataset, accum_hours, lang_pt, embed_document=embed_document, request=request
        )

    # Com runserver --reload, ThreadPoolExecutor pode falhar durante shutdown do intérprete.
    try:
        from django.conf import settings

        if settings.DEBUG:
            return _map_html_for_day_uncached(
                sel,
                dataset,
                accum_hours,
                lang_pt,
                embed_document=embed_document,
                request=request,
            )
    except Exception:
        pass

    try:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(
            _map_html_for_day_uncached,
            sel,
            dataset,
            accum_hours,
            lang_pt,
            embed_document=embed_document,
            request=request,
        )
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "home map build timeout",
                extra={"stage": "map_embed", "sel": sel, "dataset": dataset, "timeout_sec": timeout},
            )
            return "", _map_load_timeout_message(lang_pt)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    except RuntimeError as exc:
        if "shutdown" in str(exc).lower() or "schedule" in str(exc).lower():
            logger.warning("map thread pool unavailable, building inline: %s", exc)
            return _map_html_for_day_uncached(
                sel,
                dataset,
                accum_hours,
                lang_pt,
                embed_document=embed_document,
                request=request,
            )
        raise


def build_map_fragment(request) -> dict:
    import time

    t0 = time.perf_counter()
    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen, _, _ = _clamp_date_to_bq_range(p["chosen"], dataset)
    sel = chosen.isoformat()
    accum = p["accum_hours"]

    use_bq = bq_locations_probe(dataset)
    ctx = {
        "lang_pt": lang_pt,
        "use_bq": use_bq,
        "selected_date": sel,
        "accum_hours": accum,
        "map_html": "",
        "map_error": None,
        "panel_operational": "",
        **map_help_context(
            lang_pt=lang_pt,
            selected_display=chosen.strftime("%d/%m/%Y"),
            accum_hours=accum,
            use_bq=use_bq,
        ),
    }
    if not use_bq:
        from monitor_web.services.bq_status import bq_warning_message

        ctx["map_error"] = bq_warning_message(lang_pt=lang_pt, probe_failed=True)
        _log_timing("map", (time.perf_counter() - t0) * 1000)
        return ctx

    from django.urls import reverse

    from monitor_web.context_processors import _url_namespace
    from monitor_web.services.home_accumulated_alerts import indicators_period_query_string

    period_qs = indicators_period_query_string(request)
    url_ns = _url_namespace(request)
    embed = reverse(f"{url_ns}:home_map_embed") + f"?date={sel}&accum={accum}&{period_qs}"
    # Always emit susc= so iframe cache key / show= match parent preference.
    if parse_susc_show(request):
        embed += "&susc=1"
    else:
        embed += "&susc=0"
    bust = (request.GET.get("_") or "").strip()
    if bust:
        embed += f"&_={bust}"
    ctx["map_embed_url"] = embed
    _log_timing("map", (time.perf_counter() - t0) * 1000)
    return ctx


def build_map_embed_context(request) -> dict:
    """Mapa Folium completo para iframe (sem sidebar)."""
    p = parse_home_params(request)
    lang_pt = p["lang_pt"]
    dataset = p["dataset"]
    chosen, _, _ = _clamp_date_to_bq_range(p["chosen"], dataset)
    sel = chosen.isoformat()
    accum = p["accum_hours"]
    html, err = _map_html_for_day(sel, dataset, accum, lang_pt, embed_document=True, request=request)
    return {"map_html": html, "map_error": err, "lang_pt": lang_pt}


def _fig_dict(fig) -> dict | None:
    if fig is None or not len(fig.data):
        return None
    import json

    return json.loads(fig_to_json(fig))


def _build_charts_payload_uncached(p: dict) -> dict:
    """Monta figuras Plotly (dict) para API/modal — sem HTML embutido."""
    dataset = p["dataset"]
    sel = p["selected_date"]
    km_max = p["km_max"]
    fc_km = max(0, min(km_max, int(p["fc_km"])))
    window = int(os.environ.get("HOME_BQ_WINDOW_DAYS", "7"))
    out: dict = {
        "selected_date": sel,
        "fc_km": fc_km,
        "accum_hours": p["accum_hours"],
        "charts": {"heatmap": None, "km": None, "forecast": None},
        "available": False,
        "error": None,
    }
    if not bq_locations_probe(dataset):
        out["error"] = "BigQuery indisponível ou sem localizações."
        return out

    try:
        from gcp.bq_reader_clima import (
            forecast_comparison_series_bq,
            km_exceedance_count_timeseries_bq,
            merge_6h_series_for_km_bq,
            merge_daily_obs_series_for_km_bq,
        )
        import plotly.graph_objects as go
        from monitor_web.services.previsao import _build_forecast_fig, _previsao_window_iso

        chart_days = max(7, min(30, window))
        d_ex, n24, n120 = km_exceedance_count_timeseries_bq(dataset=dataset, days=chart_days)
        if d_ex:
            fb = go.Figure()
            fb.add_trace(go.Bar(x=d_ex, y=n24, name="Excedências 24 h", marker_color="rgba(251,146,60,0.9)"))
            fb.add_trace(go.Bar(x=d_ex, y=n120, name="Excedências 5 D", marker_color="rgba(129,140,248,0.9)"))
            fb.update_layout(barmode="group", height=280, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            out["charts"]["km"] = _fig_dict(fb)

        past_fc = 10
        future_fc = 15
        fc_data = forecast_comparison_series_bq(
            float(fc_km), dataset=dataset, past_days=past_fc, future_days=future_fc
        )
        ts_m6, val_m6 = merge_6h_series_for_km_bq(
            float(fc_km), dataset=dataset, past_days=past_fc, future_days=future_fc
        )
        ts_md, val_md = merge_daily_obs_series_for_km_bq(
            float(fc_km), dataset=dataset, past_days=past_fc, future_days=future_fc
        )
        date_start, date_end = _previsao_window_iso(past_fc, future_fc)
        out["charts"]["forecast"] = _build_forecast_fig(
            fc_km,
            fc_data,
            ts_m6,
            val_m6,
            ts_md,
            val_md,
            past_days=past_fc,
            future_days=future_fc,
            obs_source="merge",
            date_start=date_start,
            date_end=date_end,
        )
    except Exception as exc:
        logger.exception("home charts km/forecast")
        out["error"] = str(exc)

    try:
        from gcp.bq_reader_clima import get_model_pr_agreement_railway_heatmap_from_bq
        import plotly.graph_objects as go

        from monitor_web.services.previsao import HEATMAP_COLORSCALE

        kms, day_lbl, z_mm, dates_iso, _, _z_ag = get_model_pr_agreement_railway_heatmap_from_bq(
            dataset=dataset, horizon_days=window
        )
        if kms is not None and len(kms) and z_mm.size:
            pr_fig = go.Figure(
                data=go.Heatmap(
                    x=kms,
                    y=[str(x) for x in (dates_iso or day_lbl)],
                    z=z_mm.tolist(),
                    colorscale=HEATMAP_COLORSCALE,
                )
            )
            pr_fig.update_layout(height=380, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            out["charts"]["heatmap"] = _fig_dict(pr_fig)
    except Exception as exc:
        logger.exception("home charts heatmap")
        if not out["error"]:
            out["error"] = str(exc)

    out["available"] = any(out["charts"].values())
    return out


def build_charts_payload(request) -> dict:
    import time

    t0 = time.perf_counter()
    p = parse_home_params(request)
    chosen, _, _ = _clamp_date_to_bq_range(p["chosen"], p["dataset"])
    p = {**p, "selected_date": chosen.isoformat(), "fc_km": parse_fc_km(request.GET.get("km"), default=p["fc_km"], km_max=p["km_max"])}
    fp = bq_fingerprint()
    key = f"home_charts_v2_merge_daily_{p['dataset']}_{p['selected_date']}_{p['accum_hours']}_{p['fc_km']}_{fp}"
    ttl = int(os.environ.get("MONITOR_CHARTS_CACHE_TTL", "600"))
    payload = cached_call(key, ttl, _build_charts_payload_uncached, p)
    _log_timing("charts", (time.perf_counter() - t0) * 1000)
    return payload


def build_charts_fragment(request) -> dict:
    """Legado HTMX — mantido; preferir API JSON + JS."""
    payload = build_charts_payload(request)
    p = parse_home_params(request)
    return {
        "lang_pt": p["lang_pt"],
        "selected_date": payload["selected_date"],
        "accum_hours": payload["accum_hours"],
        "fc_km": payload["fc_km"],
        "charts_available": payload["available"],
        "charts_error": payload.get("error"),
    }
