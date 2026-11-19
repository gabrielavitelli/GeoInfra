"""Alertas Raijin side-panel charts: monthly precip + HyP3 LOS displacement."""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HYP3_GCS = "gs://efc-clima-data/obs/hyp3/hyp3_los_km_latest.json"


def _bq_dataset(dataset: str | None = None) -> str:
    return (dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"


def _client():
    try:
        from gcp.bq_load_clima import _bq_client

        return _bq_client()
    except Exception as exc:
        logger.warning("ops charts BQ client unavailable: %s", exc)
        return None


def _pfx(dataset: str) -> str:
    from gcp.bq_config import get_bq_fully_qualified_prefix

    return get_bq_fully_qualified_prefix(dataset)


def _read_gcs_json(uri: str) -> dict | list | None:
    u = (uri or "").strip()
    if not u:
        return None
    try:
        if u.startswith("gs://"):
            from google.cloud import storage

            _, _, rest = u.partition("gs://")
            bucket_name, _, blob_name = rest.partition("/")
            client = storage.Client()
            raw = client.bucket(bucket_name).blob(blob_name).download_as_bytes()
        else:
            from pathlib import Path

            raw = Path(u.replace("file://", "")).read_bytes()
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        logger.warning("ops charts GCS read failed %s: %s", u, exc)
        return None


def load_monthly_precip(
    *,
    bq_dataset: str | None = None,
    months: int = 12,
    max_stations: int = 8,
    lang_pt: bool = True,
) -> dict[str, Any]:
    """Monthly rainfall accumulation by station from ``clima_series`` precip_mm."""
    dataset = _bq_dataset(bq_dataset)
    client = _client()
    empty = {
        "available": False,
        "title": "Acúmulo mensal de chuva" if lang_pt else "Monthly rainfall",
        "subtitle": "Estações (clima_series)" if lang_pt else "Stations (clima_series)",
        "unit": "mm",
        "months": [],
        "series": [],
        "note": None,
    }
    if client is None:
        empty["note"] = "BigQuery indisponível" if lang_pt else "BigQuery unavailable"
        return empty
    pfx = _pfx(dataset)
    # Prefer observed networks; exclude MERGE grid to keep chart readable.
    # Daily-first: clima_series may hold multiple rows/day (hourly tips).
    # Sum within day, then sum days into month — avoids blind hourly inflation.
    sql = f"""
    WITH day_sum AS (
      SELECT
        date,
        location_id,
        ANY_VALUE(source) AS source,
        SUM(value) AS precip_mm
      FROM `{pfx}.clima_series`
      WHERE variable = 'precip_mm'
        AND source IN ('estacoes', 'ana', 'orion', 'zentra', 'ews')
        AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL @months MONTH)
        AND date <= CURRENT_DATE()
        AND date >= DATE '2020-01-01'
        AND date < DATE '2100-01-01'
      GROUP BY date, location_id
    ),
    monthly AS (
      SELECT
        DATE_TRUNC(date, MONTH) AS month,
        location_id,
        ANY_VALUE(source) AS source,
        SUM(precip_mm) AS precip_mm
      FROM day_sum
      GROUP BY 1, 2
    ),
    ranked AS (
      SELECT location_id, SUM(precip_mm) AS total_mm
      FROM monthly
      GROUP BY location_id
      ORDER BY total_mm DESC
      LIMIT @max_stations
    )
    SELECT
      FORMAT_DATE('%Y-%m', m.month) AS ym,
      m.location_id,
      m.source,
      m.precip_mm
    FROM monthly m
    JOIN ranked r USING (location_id)
    ORDER BY ym, m.location_id
    """
    try:
        from google.cloud import bigquery

        job_cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("months", "INT64", int(months)),
                bigquery.ScalarQueryParameter("max_stations", "INT64", int(max_stations)),
            ]
        )
        rows = list(client.query(sql, job_config=job_cfg).result())
    except Exception as exc:
        logger.warning("monthly precip query failed: %s", exc)
        empty["note"] = str(exc)[:200]
        return empty
    if not rows:
        empty["note"] = "Sem precipitação no período" if lang_pt else "No precip in window"
        return empty

    months_set: list[str] = []
    seen_m: set[str] = set()
    by_loc: dict[str, dict[str, float]] = {}
    sources: dict[str, str] = {}
    for r in rows:
        ym = str(r["ym"])
        loc = str(r["location_id"])
        if ym not in seen_m:
            seen_m.add(ym)
            months_set.append(ym)
        by_loc.setdefault(loc, {})[ym] = float(r["precip_mm"] or 0.0)
        sources[loc] = str(r["source"] or "")

    def _label(loc: str) -> str:
        raw = loc
        for pref in ("estacao_", "ews_", "zentra_", "ana_"):
            if raw.lower().startswith(pref):
                raw = raw[len(pref) :]
                break
        src = sources.get(loc, "")
        tag = {"estacoes": "BNDMET", "ana": "ANA", "orion": "EWS", "ews": "EWS", "zentra": "Zentra"}.get(
            src, src.upper() or "OBS"
        )
        return f"{tag} {raw}"

    palette = [
        "#38bdf8",
        "#34d399",
        "#fbbf24",
        "#f472b6",
        "#a78bfa",
        "#fb7185",
        "#2dd4bf",
        "#94a3b8",
    ]
    series = []
    for i, loc in enumerate(sorted(by_loc.keys(), key=lambda L: -sum(by_loc[L].values()))):
        series.append(
            {
                "id": loc,
                "label": _label(loc),
                "color": palette[i % len(palette)],
                "y": [round(by_loc[loc].get(m, 0.0), 1) for m in months_set],
            }
        )
    return {
        "available": True,
        "title": "Acúmulo mensal de chuva" if lang_pt else "Monthly rainfall",
        "subtitle": (
            "Soma mensal por estação (obs.) · não é média cega"
            if lang_pt
            else "Monthly sum per station (obs.)"
        ),
        "unit": "mm",
        "months": months_set,
        "series": series,
        "note": None,
    }


def load_hyp3_displacement(
    *,
    bq_dataset: str | None = None,
    gcs_uri: str | None = None,
    lang_pt: bool = True,
    km_lo: float = 370.0,
    km_hi: float = 380.0,
) -> dict[str, Any]:
    """LOS displacement time series + km×date heatmap (mean per km-bin)."""
    dataset = _bq_dataset(bq_dataset)
    title = "Deslocamento LOS (HyP3)" if lang_pt else "LOS displacement (HyP3)"
    subtitle = (
        "Média por km ao longo da ferrovia · não é soma cega"
        if lang_pt
        else "Mean LOS per km-bin · not a blind sum"
    )
    empty = {
        "available": False,
        "title": title,
        "subtitle": subtitle,
        "unit": "mm",
        "status": "empty",
        "timeseries": {"x": [], "y": [], "label": "LOS médio km 370–380"},
        "heatmap": {"x": [], "y": [], "z": []},
        "cumulative": {"x": [], "y": []},
        "note": (
            "Aguardando produtos HyP3 em GCS/BQ (obs/hyp3)."
            if lang_pt
            else "Waiting for HyP3 products in GCS/BQ."
        ),
        "source": None,
    }

    rows: list[dict[str, Any]] = []
    source = None

    # 1) BQ table if present
    client = _client()
    if client is not None:
        try:
            pfx = _pfx(dataset)
            sql = f"""
            SELECT acq_date, km, los_mm, los_mm_cum, job_id, job_name
            FROM `{pfx}.hyp3_los_km`
            WHERE km BETWEEN @km_lo AND @km_hi
              AND acq_date IS NOT NULL
            ORDER BY acq_date, km
            """
            from google.cloud import bigquery

            job_cfg = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("km_lo", "FLOAT64", float(km_lo)),
                    bigquery.ScalarQueryParameter("km_hi", "FLOAT64", float(km_hi)),
                ]
            )
            for r in client.query(sql, job_config=job_cfg).result():
                rows.append(
                    {
                        "acq_date": r["acq_date"].isoformat()
                        if hasattr(r["acq_date"], "isoformat")
                        else str(r["acq_date"]),
                        "km": float(r["km"]),
                        "los_mm": float(r["los_mm"]) if r["los_mm"] is not None else None,
                        "los_mm_cum": float(r["los_mm_cum"])
                        if r["los_mm_cum"] is not None
                        else None,
                    }
                )
            if rows:
                source = f"bq:{dataset}.hyp3_los_km"
        except Exception as exc:
            logger.info("hyp3 BQ table not ready: %s", exc)

    # 2) GCS fallback
    if not rows:
        uri = (gcs_uri or os.environ.get("HYP3_LOS_GCS") or _DEFAULT_HYP3_GCS).strip()
        payload = _read_gcs_json(uri)
        if isinstance(payload, dict):
            raw_rows = payload.get("rows") or []
            for r in raw_rows:
                try:
                    km = float(r.get("km"))
                except (TypeError, ValueError):
                    continue
                if not (km_lo <= km <= km_hi):
                    continue
                rows.append(
                    {
                        "acq_date": str(r.get("acq_date") or "")[:10],
                        "km": km,
                        "los_mm": float(r["los_mm"]) if r.get("los_mm") is not None else None,
                        "los_mm_cum": float(r["los_mm_cum"])
                        if r.get("los_mm_cum") is not None
                        else None,
                    }
                )
            if rows:
                source = uri

    if not rows:
        return empty

    # Aggregate: mean LOS across km bins per date (corridor mean for TS).
    by_date: dict[str, list[float]] = {}
    by_date_cum: dict[str, list[float]] = {}
    km_set: set[float] = set()
    for r in rows:
        d = r["acq_date"]
        if not d or r.get("los_mm") is None:
            continue
        by_date.setdefault(d, []).append(float(r["los_mm"]))
        if r.get("los_mm_cum") is not None:
            by_date_cum.setdefault(d, []).append(float(r["los_mm_cum"]))
        km_set.add(round(float(r["km"]), 2))

    dates = sorted(by_date.keys())
    kms = sorted(km_set)
    ts_y = [round(sum(by_date[d]) / len(by_date[d]), 3) for d in dates]
    cum_y = []
    for d in dates:
        vals = by_date_cum.get(d) or []
        cum_y.append(round(sum(vals) / len(vals), 3) if vals else None)

    # Heatmap z[i_km][j_date] = mean los_mm
    cell: dict[tuple[float, str], list[float]] = {}
    for r in rows:
        if r.get("los_mm") is None or not r.get("acq_date"):
            continue
        key = (round(float(r["km"]), 2), r["acq_date"])
        cell.setdefault(key, []).append(float(r["los_mm"]))
    z = []
    for km in kms:
        rowz = []
        for d in dates:
            vals = cell.get((km, d))
            rowz.append(round(sum(vals) / len(vals), 3) if vals else None)
        z.append(rowz)

    return {
        "available": True,
        "title": title,
        "subtitle": subtitle,
        "unit": "mm",
        "status": "ok",
        "timeseries": {
            "x": dates,
            "y": ts_y,
            "label": (
                f"LOS médio km {km_lo:.0f}–{km_hi:.0f}"
                if lang_pt
                else f"Mean LOS km {km_lo:.0f}–{km_hi:.0f}"
            ),
        },
        "cumulative": {
            "x": dates,
            "y": cum_y,
            "label": (
                "Acumulado relativo (pares)"
                if lang_pt
                else "Cumulative relative (pairs)"
            ),
        },
        "heatmap": {
            "x": dates,
            "y": [f"{k:.1f}" for k in kms],
            "z": z,
            "label": "Média LOS (mm) · km × data" if lang_pt else "Mean LOS (mm) · km × date",
        },
        # Keep note empty for UI — fluff ("N datas · fonte bq:…") removed.
        "note": None,
        "source": source,
        "n_rows": len(rows),
    }


def build_combined_los_rain(
    precip: dict[str, Any],
    hyp3: dict[str, Any],
    *,
    lang_pt: bool = True,
) -> dict[str, Any]:
    """One dual-axis chart: mean LOS (line) + corridor rainfall (bars)."""
    title = (
        "Deslocamento LOS (HyP3) + chuva"
        if lang_pt
        else "LOS displacement (HyP3) + rain"
    )
    empty = {
        "available": False,
        "title": title,
        "subtitle": None,
        "note": None,
        "los": {"x": [], "y": [], "label": "LOS médio"},
        "rain": {"x": [], "y": [], "label": "Chuva"},
    }
    ts = (hyp3 or {}).get("timeseries") or {}
    los_x = list(ts.get("x") or [])
    los_y = list(ts.get("y") or [])
    los_label = ts.get("label") or (
        "LOS médio (mm)" if lang_pt else "Mean LOS (mm)"
    )

    # Aggregate monthly precip across stations → one corridor series.
    # Median + drop absurd monthly totals (bad hourly inflation leftovers).
    months = list((precip or {}).get("months") or [])
    rain_y: list[float] = []
    series = list((precip or {}).get("series") or [])
    _MAX_MONTHLY_MM = 800.0  # >~Amazon wet-month extremes → treat as bad tip sum
    if months and series:
        for i, _m in enumerate(months):
            raw: list[float] = []
            for s in series:
                y = s.get("y") or []
                if i < len(y) and y[i] is not None:
                    try:
                        fv = float(y[i])
                    except (TypeError, ValueError):
                        continue
                    if fv >= 0.0:
                        raw.append(fv)
            # Prefer sane values; if all inflated, clip then median.
            vals = [v for v in raw if v <= _MAX_MONTHLY_MM]
            if not vals and raw:
                vals = [min(v, _MAX_MONTHLY_MM) for v in raw]
            if not vals:
                rain_y.append(0.0)
                continue
            vals.sort()
            mid = len(vals) // 2
            med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
            rain_y.append(round(med, 1))
    rain_label = (
        "Chuva mensal (mediana, mm)" if lang_pt else "Monthly rain (median, mm)"
    )

    available = bool(los_x or (months and rain_y))
    if not available:
        note = None
        if hyp3 and not hyp3.get("available"):
            note = hyp3.get("note")
        elif precip and not precip.get("available"):
            note = precip.get("note")
        empty["note"] = note
        return empty

    return {
        "available": True,
        "title": title,
        "subtitle": None,
        "note": None,
        "los": {
            "x": los_x,
            "y": los_y,
            "label": los_label,
            "unit": "mm",
        },
        "rain": {
            "x": months,
            "y": rain_y,
            "label": rain_label,
            "unit": "mm",
        },
    }


def build_ops_raijin_charts_payload(
    *,
    bq_dataset: str | None = None,
    lang_pt: bool = True,
) -> dict[str, Any]:
    precip = load_monthly_precip(bq_dataset=bq_dataset, lang_pt=lang_pt)
    hyp3 = load_hyp3_displacement(bq_dataset=bq_dataset, lang_pt=lang_pt)
    combined = build_combined_los_rain(precip, hyp3, lang_pt=lang_pt)
    return {
        "available": bool(combined.get("available")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "combined": combined,
        # Kept for back-compat / debugging — UI uses ``combined`` only.
        "precip": precip,
        "hyp3": hyp3,
    }
