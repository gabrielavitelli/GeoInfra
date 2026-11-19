"""Alertas Raijin side-panel: HyP3 LOS (pontos críticos) + chuva diária/acum. mensal.

Designed to be cheap: HyP3 from GCS snapshot first; rain = one light BQ query;
in-process TTL cache; slim JSON for a single Plotly chart.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HYP3_GCS = "gs://efc-clima-data/obs/hyp3/hyp3_los_km_latest.json"
_FRINGE_MM = 27.73
_MAX_ABS_LOS_MM = 3.0 * _FRINGE_MM  # ~83 mm for short baselines
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_S = 600.0

# Monitored / critical km points (prefs = fallback bins if exact missing).
_CRITICAL = (
    {"id": "km371", "prefs": (371.0,), "label_pt": "Ref. km 371", "label_en": "Ref. km 371", "color": "#94a3b8", "monitored": False},
    {"id": "km3725", "prefs": (372.5,), "label_pt": "Aprox. km 372,5", "label_en": "Approx. km 372.5", "color": "#34d399", "monitored": False},
    {"id": "km373", "prefs": (373.0,), "label_pt": "Corte km 373 (túnel)", "label_en": "Cut km 373 (tunnel)", "color": "#38bdf8", "monitored": True},
    {"id": "km3735", "prefs": (373.5,), "label_pt": "Corte km 373,5–374", "label_en": "Cut km 373.5–374", "color": "#a78bfa", "monitored": True},
    {"id": "km375", "prefs": (375.0, 374.5, 374.0), "label_pt": "Talude Zentra km 375", "label_en": "Zentra slope km 375", "color": "#fb7185", "monitored": True},
)


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


def _iso_to_br(iso: str, *, short: bool = False) -> str:
    """YYYY-MM-DD → DD/MM/AAAA (pt-BR); short=True → DD/MM no eixo denso."""
    s = (iso or "")[:10]
    try:
        y, m, d = s.split("-")
        return f"{d}/{m}" if short else f"{d}/{m}/{y}"
    except Exception:
        return s


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else 0.5 * (v[mid - 1] + v[mid])


def load_daily_rain_30d(
    *,
    bq_dataset: str | None = None,
    days: int = 30,
    lang_pt: bool = True,
) -> dict[str, Any]:
    """Daily median precip + month-to-date accum (light BQ query)."""
    dataset = _bq_dataset(bq_dataset)
    client = _client()
    empty = {
        "available": False,
        "x_iso": [],
        "x_br": [],
        "daily_mm": [],
        "month_accum_mm": [],
        "label_daily": "Chuva diária" if lang_pt else "Daily rain",
        "label_accum": "Acumulado mensal" if lang_pt else "Month-to-date",
        "unit_daily": "mm/d",
        "unit_accum": "mm/mês" if lang_pt else "mm/month",
    }
    if client is None:
        return empty
    pfx = _pfx(dataset)
    end = date.today()
    start = end - timedelta(days=max(7, int(days)))
    sql = f"""
    WITH day AS (
      SELECT date, location_id, SUM(value) AS precip
      FROM `{pfx}.clima_series`
      WHERE variable = 'precip_mm'
        AND source IN ('estacoes', 'ana', 'orion', 'ews')
        AND date BETWEEN @start AND @end
        AND date >= DATE '2020-01-01'
        AND date < DATE '2100-01-01'
      GROUP BY 1, 2
    ),
    clean AS (
      SELECT date, location_id, LEAST(precip, 150.0) AS precip
      FROM day
      WHERE precip IS NOT NULL AND precip >= 0
    )
    SELECT date, APPROX_QUANTILES(precip, 2)[OFFSET(1)] AS med_mm
    FROM clean
    GROUP BY 1
    ORDER BY 1
    """
    try:
        from google.cloud import bigquery

        job_cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", start.isoformat()),
                bigquery.ScalarQueryParameter("end", "DATE", end.isoformat()),
            ]
        )
        rows = list(client.query(sql, job_config=job_cfg).result())
    except Exception as exc:
        logger.warning("daily rain query failed: %s", exc)
        return empty
    if not rows:
        return empty

    x_iso: list[str] = []
    daily: list[float] = []
    accum: list[float] = []
    run = 0.0
    cur_month: str | None = None
    for r in rows:
        iso = r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"])[:10]
        mm = float(r["med_mm"] or 0.0)
        mkey = iso[:7]
        if mkey != cur_month:
            cur_month = mkey
            run = 0.0
        run += mm
        x_iso.append(iso)
        daily.append(round(mm, 1))
        accum.append(round(run, 1))
    return {
        "available": True,
        "x_iso": x_iso,
        "x_br": [_iso_to_br(x, short=True) for x in x_iso],
        "daily_mm": daily,
        "month_accum_mm": accum,
        "label_daily": "Chuva diária" if lang_pt else "Daily rain",
        "label_accum": "Acumulado mensal" if lang_pt else "Month-to-date",
        "unit_daily": "mm/d",
        "unit_accum": "mm/mês" if lang_pt else "mm/month",
    }


def _load_hyp3_bundle(
    *, gcs_uri: str | None = None, bq_dataset: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prefer GCS snapshot (cheap); optional BQ fallback. Returns (rows, meta)."""
    uri = (gcs_uri or os.environ.get("HYP3_LOS_GCS") or _DEFAULT_HYP3_GCS).strip()
    payload = _read_gcs_json(uri)
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    if isinstance(payload, dict):
        meta = {
            "aggregation": payload.get("aggregation"),
            "method": payload.get("method") or {},
            "generated_at": payload.get("generated_at"),
        }
        for r in payload.get("rows") or []:
            try:
                rows.append(
                    {
                        "acq_date": str(r.get("acq_date") or "")[:10],
                        "ref_date": str(r.get("ref_date") or "")[:10],
                        "km": float(r["km"]),
                        "los_mm": float(r["los_mm"]) if r.get("los_mm") is not None else None,
                        "job_id": str(r.get("job_id") or ""),
                        "aggregation": str(r.get("aggregation") or meta.get("aggregation") or ""),
                    }
                )
            except (TypeError, ValueError, KeyError):
                continue
    if rows:
        # Prefer γ≥0.30 / 500 m disk rows when mixed with legacy 3×3 samples.
        disk = [r for r in rows if "gamma" in (r.get("aggregation") or "").lower() or "disk" in (r.get("aggregation") or "").lower()]
        return (disk or rows), meta

    client = _client()
    if client is None:
        return [], meta
    dataset = _bq_dataset(bq_dataset)
    try:
        pfx = _pfx(dataset)
        sql = f"""
        SELECT acq_date, ref_date, km, los_mm, job_id, aggregation
        FROM `{pfx}.hyp3_los_km`
        WHERE km BETWEEN 370 AND 376
          AND acq_date IS NOT NULL
        ORDER BY acq_date, km
        """
        for r in client.query(sql).result():
            rows.append(
                {
                    "acq_date": r["acq_date"].isoformat()
                    if hasattr(r["acq_date"], "isoformat")
                    else str(r["acq_date"])[:10],
                    "ref_date": r["ref_date"].isoformat()
                    if r["ref_date"] is not None and hasattr(r["ref_date"], "isoformat")
                    else str(r["ref_date"] or "")[:10],
                    "km": float(r["km"]),
                    "los_mm": float(r["los_mm"]) if r["los_mm"] is not None else None,
                    "job_id": str(r["job_id"] or ""),
                    "aggregation": str(r.get("aggregation") or ""),
                }
            )
        disk = [r for r in rows if "gamma" in (r.get("aggregation") or "").lower() or "disk" in (r.get("aggregation") or "").lower()]
        return (disk or rows), meta
    except Exception as exc:
        logger.info("hyp3 BQ fallback unavailable: %s", exc)
    return rows, meta


def _load_hyp3_rows(*, gcs_uri: str | None = None, bq_dataset: str | None = None) -> list[dict[str, Any]]:
    rows, _meta = _load_hyp3_bundle(gcs_uri=gcs_uri, bq_dataset=bq_dataset)
    return rows


def build_critical_los_series(
    *,
    days: int = 30,
    lang_pt: bool = True,
    bq_dataset: str | None = None,
) -> dict[str, Any]:
    """Short-baseline QC + cumulative LOS for 5 critical points (+ min/max band)."""
    end = date.today()
    start = end - timedelta(days=max(7, int(days)))
    rows, hyp3_meta = _load_hyp3_bundle(bq_dataset=bq_dataset)
    method = hyp3_meta.get("method") or {}
    g_raw = float(method.get("gamma_min", 0.30))
    r_raw = float(method.get("radius_m", 500))
    g_pt = f"{g_raw:.2f}".replace(".", ",")
    method_note = (
        (
            f"γ≥{g_pt} · raio {r_raw:g} m"
            + (" · disco (sem anel)" if float(method.get("ring_inner_m") or 0) <= 0 else " · anel")
        )
        if lang_pt
        else (
            f"γ≥{g_raw:g} · {r_raw:g} m radius"
            + (" · disk (no ring)" if float(method.get("ring_inner_m") or 0) <= 0 else " · ring")
        )
    )
    empty = {
        "available": False,
        "points": [],
        "band": {"x_iso": [], "x_br": [], "ymin": [], "ymax": []},
        "note": (
            "Aguardando pares HyP3 (12 d) no corredor."
            if lang_pt
            else "Waiting for HyP3 12-day pairs."
        ),
        "method_note": method_note,
    }
    if not rows:
        return empty

    # Group by job → (ref, acq, by_km)
    by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_job[r.get("job_id") or f"{r.get('ref_date')}_{r.get('acq_date')}"].append(r)

    cands: list[dict[str, Any]] = []
    for jid, rs in by_job.items():
        ref = str(rs[0].get("ref_date") or "")[:10]
        acq = str(rs[0].get("acq_date") or "")[:10]
        try:
            dt_days = (date.fromisoformat(acq) - date.fromisoformat(ref)).days
        except Exception:
            continue
        by_km: dict[float, float] = {}
        for r in rs:
            if r.get("los_mm") is None:
                continue
            by_km[round(float(r["km"]), 1)] = float(r["los_mm"])
        if not by_km:
            continue
        med = _median(list(by_km.values()))
        if med is None:
            continue
        if not (6 <= dt_days <= 36):
            continue
        if abs(med) > _MAX_ABS_LOS_MM or any(abs(v) > _MAX_ABS_LOS_MM * 1.5 for v in by_km.values()):
            continue
        cands.append({"acq": acq, "dt": dt_days, "by_km": by_km, "job_id": jid})

    by_acq: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cands:
        by_acq[c["acq"]].append(c)

    chosen: dict[str, dict[str, float]] = {}
    for acq, lst in by_acq.items():
        best_dt = min(x["dt"] for x in lst)
        short = [x for x in lst if x["dt"] == best_dt]
        kms = set().union(*(x["by_km"].keys() for x in short))
        merged_km: dict[float, float] = {}
        for km in kms:
            vals = [x["by_km"][km] for x in short if km in x["by_km"]]
            if vals:
                merged_km[km] = sum(vals) / len(vals)
        chosen[acq] = merged_km

    dates = [d for d in sorted(chosen.keys()) if d >= start.isoformat()]
    if not dates:
        return empty

    def _pick(by_km: dict[float, float], prefs: tuple[float, ...]) -> float | None:
        for k in prefs:
            if k in by_km:
                return float(by_km[k])
        return None

    points_out: list[dict[str, Any]] = []
    # Cumulative per critical point
    series_vals: dict[str, list[float | None]] = {p["id"]: [] for p in _CRITICAL}
    running = {p["id"]: 0.0 for p in _CRITICAL}
    present = {p["id"]: False for p in _CRITICAL}
    for d in dates:
        by_km = chosen[d]
        for p in _CRITICAL:
            v = _pick(by_km, p["prefs"])
            if v is None:
                series_vals[p["id"]].append(None)
                continue
            running[p["id"]] += v
            present[p["id"]] = True
            series_vals[p["id"]].append(round(running[p["id"]], 2))

    # Eixo: DD/MM (legível); hover no JS usa x_iso → DD/MM/AAAA.
    for p in _CRITICAL:
        if not present[p["id"]]:
            continue
        # Forward-fill gaps for line continuity (keep None→skip in JS alternatively).
        ys: list[float] = []
        xs_iso: list[str] = []
        xs_br: list[str] = []
        last: float | None = None
        for d, y in zip(dates, series_vals[p["id"]]):
            if y is None:
                continue
            last = y
            xs_iso.append(d)
            xs_br.append(_iso_to_br(d, short=True))
            ys.append(y)
        if not ys:
            continue
        points_out.append(
            {
                "id": p["id"],
                "label": p["label_pt"] if lang_pt else p["label_en"],
                "color": p["color"],
                "monitored": p["monitored"],
                "x_iso": xs_iso,
                "x_br": xs_br,
                "y": ys,
            }
        )

    # Band = min/max across critical points at each date (covers all lines).
    ymin: list[float] = []
    ymax: list[float] = []
    band_x_iso: list[str] = []
    band_x_br: list[str] = []
    for i, d in enumerate(dates):
        vals = []
        for p in _CRITICAL:
            y = series_vals[p["id"]][i] if i < len(series_vals[p["id"]]) else None
            if y is not None:
                vals.append(y)
        if not vals:
            continue
        band_x_iso.append(d)
        band_x_br.append(_iso_to_br(d, short=True))
        ymin.append(round(min(vals), 2))
        ymax.append(round(max(vals), 2))

    return {
        "available": bool(points_out),
        "points": points_out,
        "band": {
            "x_iso": band_x_iso,
            "x_br": band_x_br,
            "ymin": ymin,
            "ymax": ymax,
        },
        "note": None,
        "method_note": method_note,
        "unit": "mm",
        "unit_label": "LOS acum. (mm)" if lang_pt else "Cum. LOS (mm)",
    }


def build_combined_los_rain(
    precip: dict[str, Any] | None = None,
    hyp3: dict[str, Any] | None = None,
    *,
    lang_pt: bool = True,
    rain: dict[str, Any] | None = None,
    los: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Slim combined payload for the right panel (critical LOS + daily rain).

    ``precip``/``hyp3`` kept for back-compat with older tests; preferred path
    passes ``rain`` + ``los`` from the new builders.
    """
    title = "Deslocamento LOS (HyP3)" if lang_pt else "LOS displacement (HyP3)"

    # Legacy path (tests): single LOS mean + monthly median rain.
    if los is None and hyp3 is not None:
        ts = (hyp3 or {}).get("timeseries") or {}
        months = list((precip or {}).get("months") or [])
        series = list((precip or {}).get("series") or [])
        rain_y: list[float] = []
        _MAX = 800.0
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
                vals = [v for v in raw if v <= _MAX]
                if not vals and raw:
                    vals = [min(v, _MAX) for v in raw]
                if not vals:
                    rain_y.append(0.0)
                    continue
                vals.sort()
                mid = len(vals) // 2
                med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
                rain_y.append(round(med, 1))
        los_x = list(ts.get("x") or [])
        los_y = list(ts.get("y") or [])
        available = bool(los_x or (months and rain_y))
        if not available:
            return {
                "available": False,
                "title": title,
                "note": (hyp3 or {}).get("note") or (precip or {}).get("note"),
                "points": [],
                "band": {"x_br": [], "ymin": [], "ymax": []},
                "rain_daily": {"x_br": [], "y": []},
                "rain_accum": {"x_br": [], "y": []},
                # legacy keys
                "los": {"x": los_x, "y": los_y, "label": "LOS médio"},
                "rain": {
                    "x": months,
                    "y": rain_y,
                    "label": "Chuva mensal (mediana, mm)" if lang_pt else "Monthly rain",
                },
            }
        return {
            "available": True,
            "title": title,
            "note": None,
            "points": [
                {
                    "id": "legacy",
                    "label": ts.get("label") or ("LOS médio" if lang_pt else "Mean LOS"),
                    "color": "#f97316",
                    "monitored": True,
                    "x_iso": los_x,
                    "x_br": [_iso_to_br(x) for x in los_x],
                    "y": los_y,
                }
            ]
            if los_x
            else [],
            "band": {"x_br": [], "ymin": [], "ymax": []},
            "rain_daily": {
                "x_iso": months,
                "x_br": months,
                "y": rain_y,
                "label": "Chuva mensal" if lang_pt else "Monthly rain",
                "unit": "mm/mês" if lang_pt else "mm/month",
            },
            "rain_accum": {"x_br": [], "y": [], "label": "", "unit": "mm/mês"},
            "axes": {
                "y_los": "LOS (mm)" if lang_pt else "LOS (mm)",
                "y_rain": "Chuva (mm/mês)" if lang_pt else "Rain (mm/month)",
            },
            "los": {
                "x": los_x,
                "y": los_y,
                "label": ts.get("label") or "LOS médio",
                "unit": "mm",
            },
            "rain": {
                "x": months,
                "y": rain_y,
                "label": "Chuva mensal (mediana, mm)" if lang_pt else "Monthly rain",
                "unit": "mm",
            },
        }

    rain = rain or {}
    los = los or {}
    points = list(los.get("points") or [])
    band = los.get("band") or {"x_br": [], "ymin": [], "ymax": []}
    method_note = los.get("method_note") or (
        "γ≥0,30 · raio 500 m · disco (sem anel)"
        if lang_pt
        else "γ≥0.30 · 500 m radius · disk (no ring)"
    )
    available = bool(points or rain.get("available"))
    if not available:
        return {
            "available": False,
            "title": title,
            "note": los.get("note") or rain.get("note"),
            "method_note": method_note,
            "points": [],
            "band": band,
            "rain_daily": {"x_br": [], "y": []},
            "rain_accum": {"x_br": [], "y": []},
            "axes": {
                "y_los": "LOS acum. (mm)" if lang_pt else "Cum. LOS (mm)",
                "y_rain": "Chuva (mm/d · mm/mês)" if lang_pt else "Rain (mm/d · mm/month)",
            },
        }

    return {
        "available": True,
        "title": title,
        "note": None,
        "method_note": method_note,
        "points": points,
        "band": band,
        "rain_daily": {
            "x_iso": rain.get("x_iso") or [],
            "x_br": rain.get("x_br") or [],
            "y": rain.get("daily_mm") or [],
            "label": rain.get("label_daily") or ("Chuva diária" if lang_pt else "Daily rain"),
            "unit": rain.get("unit_daily") or "mm/d",
        },
        "rain_accum": {
            "x_iso": rain.get("x_iso") or [],
            "x_br": rain.get("x_br") or [],
            "y": rain.get("month_accum_mm") or [],
            "label": rain.get("label_accum") or ("Acumulado mensal" if lang_pt else "Month-to-date"),
            "unit": rain.get("unit_accum") or ("mm/mês" if lang_pt else "mm/month"),
        },
        "axes": {
            "y_los": los.get("unit_label")
            or ("LOS acum. (mm)" if lang_pt else "Cum. LOS (mm)"),
            "y_rain": "Chuva (mm/d · mm/mês)" if lang_pt else "Rain (mm/d · mm/month)",
        },
        # Keep tiny legacy keys so old callers don't crash.
        "los": {"x": [], "y": [], "label": "LOS"},
        "rain": {"x": [], "y": [], "label": "Chuva"},
    }


def build_ops_raijin_charts_payload(
    *,
    bq_dataset: str | None = None,
    lang_pt: bool = True,
) -> dict[str, Any]:
    cache_key = f"{_bq_dataset(bq_dataset)}:{int(lang_pt)}"
    now = time.time()
    hit = _CACHE.get(cache_key)
    if hit and now - hit[0] < _CACHE_TTL_S:
        return hit[1]

    rain = load_daily_rain_30d(bq_dataset=bq_dataset, lang_pt=lang_pt)
    los = build_critical_los_series(bq_dataset=bq_dataset, lang_pt=lang_pt)
    combined = build_combined_los_rain(rain=rain, los=los, lang_pt=lang_pt)
    payload = {
        "available": bool(combined.get("available")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "combined": combined,
    }
    _CACHE[cache_key] = (now, payload)
    return payload
