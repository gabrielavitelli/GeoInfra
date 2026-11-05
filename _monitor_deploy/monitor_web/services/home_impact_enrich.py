"""Enriquece o dict de impacto operacional com nomes de estação e km EFC."""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

from lib_efc import _closest_km_on_line, load_ana_estacoes, load_bndmet_estacoes
from monitor_web.services.cache_keys import bq_fingerprint, cached_call
from monitor_web.services.home_accumulated_alerts import parse_accumulated_period, period_label_pt


def _impact_cache_ttl() -> int:
    """TTL do cache de impacto. Default 1h (a query pesa ~18 GB; com 5 min o warmup
    a cada 15 min re-executava sempre). Ajustável via MONITOR_IMPACT_CACHE_TTL."""
    try:
        return int(os.environ.get("MONITOR_IMPACT_CACHE_TTL", "3600"))
    except ValueError:
        return 3600


def _code_variants(code: str) -> set[str]:
    c = str(code or "").strip()
    if not c:
        return set()
    out = {c, c.upper(), c.lower()}
    if c.isdigit():
        stripped = c.lstrip("0") or "0"
        out.add(stripped)
        out.add(stripped.zfill(8))
        out.add(c.zfill(8))
    return {x for x in out if x}


def _build_station_lookup(df: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if df is None or df.empty:
        return {}
    cod_c = next((c for c in ("codEstacao", "codigo", "id") if c in df.columns), None)
    if not cod_c:
        return {}
    nome_c = next((c for c in ("nome", "name") if c in df.columns), None)
    lat_c = next((c for c in df.columns if "lat" in str(c).lower()), None)
    lon_c = next((c for c in df.columns if "lon" in str(c).lower()), None)
    km_c = next((c for c in ("km_efc", "km", "km_estacao") if c in df.columns), None)
    mun_c = next(
        (c for c in ("municipio_uf", "municipio", "municipality", "cidade") if c in df.columns),
        None,
    )
    lookup: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        cod = str(row[cod_c]).strip()
        if not cod or cod.lower() == "nan":
            continue
        nome = str(row[nome_c]).strip() if nome_c and pd.notna(row.get(nome_c)) else cod
        lat = pd.to_numeric(row[lat_c], errors="coerce") if lat_c else None
        lon = pd.to_numeric(row[lon_c], errors="coerce") if lon_c else None
        km_pre = None
        if km_c and pd.notna(row.get(km_c)):
            km_pre = int(round(float(row[km_c])))
        mun = ""
        if mun_c and pd.notna(row.get(mun_c)):
            mun = str(row[mun_c]).strip()
        entry = {
            "code": cod,
            "name": nome,
            "lat": lat,
            "lon": lon,
            "km_efc": km_pre,
            "municipio": mun or None,
        }
        for key in _code_variants(cod):
            lookup[key] = entry
    return lookup


def _lookup_station(station_id: str, *lookups: dict[str, dict]) -> dict[str, Any] | None:
    sid = str(station_id or "").strip()
    if not sid:
        return None
    for key in _code_variants(sid):
        for table in lookups:
            if key in table:
                hit = dict(table[key])
                hit["code"] = hit.get("code") or sid
                return hit
    return {"code": sid, "name": sid, "lat": None, "lon": None, "km_efc": None}


def _resolve_km_efc(entry: dict[str, Any], main_line, cum_km) -> int | None:
    if entry.get("km_efc") is not None:
        return int(entry["km_efc"])
    lat, lon = entry.get("lat"), entry.get("lon")
    if (
        main_line is not None
        and cum_km is not None
        and lat is not None
        and lon is not None
        and pd.notna(lat)
        and pd.notna(lon)
    ):
        km = _closest_km_on_line(main_line, cum_km, float(lat), float(lon))
        if km is not None and pd.notna(km):
            return int(round(float(km)))
    return None


def _station_labels(entry: dict[str, Any] | None, *, lang_pt: bool, network: str) -> dict[str, str]:
    if not entry:
        return {}
    name = str(entry.get("name") or entry.get("code") or "").strip()
    code = str(entry.get("code") or "").strip()
    km = entry.get("km_efc")
    net = network.upper()
    if lang_pt:
        primary = name or code or "—"
        if code and name and code.upper() != name.upper():
            secondary = f"cód. {code}"
        elif code:
            secondary = f"cód. {code}"
        else:
            secondary = ""
        if km is not None:
            km_part = f"km EFC {int(km)}"
            secondary = f"{secondary} · {km_part}" if secondary else km_part
        title = f"Estação {net}"
    else:
        primary = name or code or "—"
        if code and name and code.upper() != name.upper():
            secondary = f"code {code}"
        elif code:
            secondary = f"code {code}"
        else:
            secondary = ""
        if km is not None:
            km_part = f"EFC km {int(km)}"
            secondary = f"{secondary} · {km_part}" if secondary else km_part
        title = f"{net} station"
    return {"title": title, "primary": primary, "secondary": secondary}


def _cached_lookups(bq_dataset: str | None = None):
    ds = bq_dataset or ""

    def _load():
        from monitor_web.services.home import _cached_station_catalog

        try:
            ews = _build_station_lookup(_cached_station_catalog("orion", ds))
        except Exception:
            ews = {}
        return {
            "bndmet": _build_station_lookup(_cached_station_catalog("bndmet", ds)),
            "ana": _build_station_lookup(_cached_station_catalog("ana", ds)),
            "ews": ews,
        }

    return cached_call(f"home_impact_station_lookups_v3_{ds}", 3600, _load)


def enrich_home_impact(
    impact: dict,
    *,
    lang_pt: bool,
    main_line=None,
    cum_km=None,
    bq_dataset: str | None = None,
) -> dict:
    """Adiciona rótulos legíveis para estações BNDMET e ANA no painel Home."""
    if not impact or not impact.get("available"):
        return impact
    out = dict(impact)
    tables = _cached_lookups(bq_dataset)

    b_id = str(out.get("bndmet_max_24_id") or "").strip()
    if b_id:
        ent = _lookup_station(b_id, tables["bndmet"], tables["ana"])
        if ent is not None:
            ent["km_efc"] = _resolve_km_efc(ent, main_line, cum_km)
            out["bndmet_station"] = _station_labels(ent, lang_pt=lang_pt, network="BNDMET")

    a_id = str(out.get("ana_max_24_id") or "").strip()
    if a_id:
        ent = _lookup_station(a_id, tables["ana"], tables["bndmet"])
        if ent is not None:
            ent["km_efc"] = _resolve_km_efc(ent, main_line, cum_km)
            out["ana_station"] = _station_labels(ent, lang_pt=lang_pt, network="ANA")

    return out


def home_impact_for_request(
    request,
    *,
    dataset: str,
    sel: str,
    lang_pt: bool,
    main_line=None,
    cum_km=None,
    ttl: int | None = None,
):
    """Impacto operacional do dia selecionado (cache). Evita scan de período (lento)."""
    from gcp.bq_reader_extended import home_operational_impact_bq

    if ttl is None:
        ttl = _impact_cache_ttl()
    d_start, d_end, period_key = parse_accumulated_period(request, date_end_cap=sel)
    fp = bq_fingerprint()
    # Chave por dia — first paint rápido; período agregado era ~dezenas de GB.
    cache_key = f"impact_day_{dataset}_{sel}_{fp}"

    def _load():
        impact = home_operational_impact_bq(sel, dataset=dataset)
        if impact.get("available"):
            impact = dict(impact)
            impact["period_label_pt"] = period_label_pt(period_key, d_start, d_end)
            if not lang_pt:
                impact["period_label"] = impact["period_label_pt"]
            impact["scope"] = "day"
        return impact

    impact = cached_call(cache_key, ttl, _load)
    return enrich_home_impact(
        impact,
        lang_pt=lang_pt,
        main_line=main_line,
        cum_km=cum_km,
        bq_dataset=dataset,
    )


def home_impact_for_request_safe(
    request,
    *,
    dataset: str,
    sel: str,
    lang_pt: bool,
    main_line=None,
    cum_km=None,
    ttl: int | None = None,
):
    """Limita espera BQ para não travar a Visão Geral (staff e portal)."""
    import concurrent.futures
    import logging
    import os

    if ttl is None:
        ttl = _impact_cache_ttl()

    timeout = int(
        os.environ.get("MONITOR_IMPACT_BQ_TIMEOUT_SEC")
        or os.environ.get("MONITOR_PORTAL_BQ_TIMEOUT_SEC")
        or "60"
    )
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(
        home_impact_for_request,
        request,
        dataset=dataset,
        sel=sel,
        lang_pt=lang_pt,
        main_line=main_line,
        cum_km=cum_km,
        ttl=ttl,
    )
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logging.getLogger(__name__).warning(
            "impact_bq_timeout dataset=%s sel=%s sec=%s",
            dataset,
            sel,
            timeout,
        )
        return {"available": False}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
