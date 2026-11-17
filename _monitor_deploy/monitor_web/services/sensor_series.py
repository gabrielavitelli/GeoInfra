"""Time-series JSON for Zentra / Orion sensors (clima_series).

Port/série selector uses sensor families from ``zentra_devices.sensors``
(TEROS / ATMOS / health) because ``clima_series`` does not yet store port_num.
"""
from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

_WP_SENTINEL = -90000.0

VAR_LABELS_PT: dict[str, str] = {
    "soil_vwc": "VWC solo",
    "water_potential_kpa": "Pot. água",
    "soil_temp_c": "Temp solo",
    "precip_mm": "Precip",
    "temp_c": "Temp ar",
    "rh_pct": "UR",
    "pressure_hpa": "Pressão",
    "battery_pct": "Bateria",
    "battery_v": "Bateria V",
    "signal_pct": "Sinal",
}

VAR_UNITS: dict[str, str] = {
    "soil_vwc": "m³/m³",
    "water_potential_kpa": "kPa",
    "soil_temp_c": "°C",
    "precip_mm": "mm",
    "temp_c": "°C",
    "rh_pct": "%",
    "pressure_hpa": "hPa",
    "battery_pct": "%",
    "battery_v": "V",
    "signal_pct": "%",
}

# Sensor-family → variables (port/série menus).
FAMILY_VARS: dict[str, tuple[str, ...]] = {
    "TEROS 12": ("soil_vwc", "soil_temp_c"),
    "TEROS 21": ("water_potential_kpa",),
    "ATMOS": ("precip_mm", "temp_c", "rh_pct", "pressure_hpa"),
    "Logger": ("battery_pct", "battery_v", "signal_pct"),
}

FAMILY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"TEROS\s*12|TEROS\s*10|TEROS\s*11|TEROS\s*54", re.I), "TEROS 12"),
    (re.compile(r"TEROS\s*21|TEROS\s*22|MPS", re.I), "TEROS 21"),
    (re.compile(r"ATMOS|Rain|Barometer", re.I), "ATMOS"),
]

CHART_VARS_PRIORITY: tuple[str, ...] = (
    "soil_vwc",
    "water_potential_kpa",
    "precip_mm",
    "soil_temp_c",
    "temp_c",
    "rh_pct",
    "pressure_hpa",
    "battery_pct",
    "signal_pct",
)


def _bq_dataset(dataset: str | None = None) -> str:
    return (dataset or os.environ.get("BQ_DATASET") or "efc_clima").strip() or "efc_clima"


def _client():
    try:
        from gcp.bq_load_clima import _bq_client

        return _bq_client()
    except Exception:
        return None


def _pfx(dataset: str) -> str:
    from gcp.bq_config import get_bq_fully_qualified_prefix

    return get_bq_fully_qualified_prefix(dataset)


def normalize_family(sensor_name: str | None) -> str | None:
    if not sensor_name:
        return None
    for pat, fam in FAMILY_PATTERNS:
        if pat.search(str(sensor_name)):
            return fam
    return None


def families_from_sensors(sensors: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in sensors or []:
        fam = normalize_family(s)
        if fam and fam not in seen:
            seen.add(fam)
            out.append(fam)
    if "Logger" not in seen:
        out.append("Logger")
    return out or ["Logger"]


def ports_for_device(sensors: list[str] | None, n_ports: int | None = None) -> list[dict[str, Any]]:
    """Compact port/série menu: one entry per sensor family (+ Logger)."""
    families = families_from_sensors(sensors)
    ports: list[dict[str, Any]] = []
    for fam in families:
        vars_ = FAMILY_VARS.get(fam, ())
        ports.append(
            {
                "id": fam,
                "label": fam,
                "variables": [
                    {
                        "id": v,
                        "label": VAR_LABELS_PT.get(v, v),
                        "unit": VAR_UNITS.get(v, ""),
                    }
                    for v in vars_
                ],
            }
        )
    _ = n_ports  # reserved when ingest encodes port_num in clima_series
    return ports


def default_variable_for_ports(ports: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (family_id, variable_id) — prefer geotech (VWC / pot.) over meteo."""
    for pref in CHART_VARS_PRIORITY:
        for fam in ports:
            for v in fam.get("variables") or []:
                if v.get("id") == pref:
                    return str(fam["id"]), pref
    for fam in ports:
        vars_ = fam.get("variables") or []
        if vars_:
            return str(fam["id"]), str(vars_[0]["id"])
    return "Logger", "battery_pct"


def _parse_days(raw: str | int | None) -> int:
    try:
        d = int(raw) if raw is not None else 7
    except (TypeError, ValueError):
        d = 7
    if d <= 1:
        return 1
    if d <= 7:
        return 7
    return 30


def load_device_meta(location_id: str, *, bq_dataset: str | None = None) -> dict[str, Any] | None:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None or not location_id:
        return None
    try:
        pfx = _pfx(ds)
        sql = f"""
            SELECT location_id, name, lat, lon, sensors, n_ports, status, asset_class, model,
                   last_measurement
            FROM `{pfx}.zentra_devices`
            WHERE location_id = @loc
            LIMIT 1
        """
        from google.cloud import bigquery as bq

        job = client.query(
            sql,
            job_config=bq.QueryJobConfig(
                query_parameters=[bq.ScalarQueryParameter("loc", "STRING", location_id)]
            ),
        )
        rows = list(job.result())
        if not rows:
            return None
        r = rows[0]
        sensors = r.get("sensors")
        if hasattr(sensors, "tolist"):
            sensors = list(sensors.tolist())
        elif not isinstance(sensors, (list, tuple)):
            sensors = []
        return {
            "location_id": str(r.get("location_id") or ""),
            "name": str(r.get("name") or r.get("location_id") or ""),
            "lat": float(r["lat"]) if r.get("lat") is not None else None,
            "lon": float(r["lon"]) if r.get("lon") is not None else None,
            "sensors": [str(s) for s in sensors if s],
            "n_ports": int(r["n_ports"]) if r.get("n_ports") is not None else None,
            "status": str(r.get("status") or ""),
            "asset_class": str(r.get("asset_class") or ""),
            "model": str(r.get("model") or ""),
            "source": "zentra",
            "last_measurement": r.get("last_measurement"),
        }
    except Exception:
        return None


def load_orion_device_meta(location_id: str, *, bq_dataset: str | None = None) -> dict[str, Any] | None:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None or not location_id:
        return None
    try:
        pfx = _pfx(ds)
        sql = f"""
            SELECT location_id, name, lat, lon, status, station_id, last_upload
            FROM `{pfx}.orion_devices`
            WHERE location_id = @loc OR station_id = @st
            LIMIT 1
        """
        from google.cloud import bigquery as bq

        st = location_id.replace("ews_", "") if location_id.startswith("ews_") else location_id
        job = client.query(
            sql,
            job_config=bq.QueryJobConfig(
                query_parameters=[
                    bq.ScalarQueryParameter("loc", "STRING", location_id),
                    bq.ScalarQueryParameter("st", "STRING", st),
                ]
            ),
        )
        rows = list(job.result())
        if not rows:
            return None
        r = rows[0]
        return {
            "location_id": str(r.get("location_id") or location_id),
            "name": str(r.get("name") or r.get("station_id") or location_id),
            "lat": float(r["lat"]) if r.get("lat") is not None else None,
            "lon": float(r["lon"]) if r.get("lon") is not None else None,
            "sensors": ["Orion"],
            "n_ports": None,
            "status": str(r.get("status") or ""),
            "asset_class": "orion",
            "model": "Orion",
            "source": "orion",
            "last_measurement": None,
        }
    except Exception:
        return None


def _available_variables(
    location_id: str,
    source: str,
    *,
    bq_dataset: str | None = None,
    days: int = 30,
) -> list[str]:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None:
        return []
    try:
        pfx = _pfx(ds)
        start = date.today() - timedelta(days=max(days, 1))
        sql = f"""
            SELECT DISTINCT variable
            FROM `{pfx}.clima_series`
            WHERE source = @src
              AND location_id = @loc
              AND date >= @start
        """
        from google.cloud import bigquery as bq

        job = client.query(
            sql,
            job_config=bq.QueryJobConfig(
                query_parameters=[
                    bq.ScalarQueryParameter("src", "STRING", source),
                    bq.ScalarQueryParameter("loc", "STRING", location_id),
                    bq.ScalarQueryParameter("start", "DATE", start),
                ]
            ),
        )
        vars_ = [str(r["variable"]) for r in job.result() if r.get("variable")]
        # stable priority order
        rank = {v: i for i, v in enumerate(CHART_VARS_PRIORITY)}
        vars_.sort(key=lambda v: (rank.get(v, 99), v))
        return vars_
    except Exception:
        return []


def fetch_series_points(
    *,
    location_id: str,
    source: str = "zentra",
    variable: str = "soil_vwc",
    days: int = 7,
    bq_dataset: str | None = None,
    limit: int = 2500,
) -> list[dict[str, Any]]:
    """Daily mean series from clima_series (source=zentra|orion).

    Measurement timestamps are not yet stored in ``clima_series`` (only civil
    ``date``); batch ``ingested_at`` is not a usable time axis. Daily means are
    the honest chart until ingest writes ``valid_time``.
    """
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None or not location_id or not variable:
        return []
    days = _parse_days(days)
    # 24h window still needs ≥1 calendar day of means
    start = date.today() - timedelta(days=max(days, 1))
    try:
        pfx = _pfx(ds)
        from google.cloud import bigquery as bq

        sql = f"""
            SELECT
              date AS d,
              AVG(value) AS value,
              COUNT(*) AS n
            FROM `{pfx}.clima_series`
            WHERE source = @src
              AND location_id = @loc
              AND variable = @var
              AND date >= @start
              AND value IS NOT NULL
              AND (@var != 'water_potential_kpa' OR value > {_WP_SENTINEL})
            GROUP BY 1
            ORDER BY 1
            LIMIT @lim
        """
        job = client.query(
            sql,
            job_config=bq.QueryJobConfig(
                query_parameters=[
                    bq.ScalarQueryParameter("src", "STRING", source),
                    bq.ScalarQueryParameter("loc", "STRING", location_id),
                    bq.ScalarQueryParameter("var", "STRING", variable),
                    bq.ScalarQueryParameter("start", "DATE", start),
                    bq.ScalarQueryParameter("lim", "INT64", int(limit)),
                ]
            ),
        )
        out: list[dict[str, Any]] = []
        for r in job.result():
            d = r.get("d")
            v = r.get("value")
            if v is None or d is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            if isinstance(d, date) and not isinstance(d, datetime):
                ts = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc).isoformat()
            else:
                ts = d.isoformat() if hasattr(d, "isoformat") else str(d)
            out.append({"t": ts, "v": fv, "n": int(r.get("n") or 0)})
        return out
    except Exception:
        return []


def fetch_latest_values(
    *,
    location_id: str,
    source: str = "zentra",
    variables: list[str] | None = None,
    bq_dataset: str | None = None,
) -> list[dict[str, Any]]:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is None:
        return []
    vars_ = variables or list(CHART_VARS_PRIORITY)
    try:
        pfx = _pfx(ds)
        var_list = ", ".join(f"'{v}'" for v in vars_)
        sql = f"""
            WITH ranked AS (
              SELECT location_id, variable, value, date, ingested_at,
                ROW_NUMBER() OVER (
                  PARTITION BY variable
                  ORDER BY ingested_at DESC
                ) AS rn
              FROM `{pfx}.clima_series`
              WHERE source = @src
                AND location_id = @loc
                AND variable IN ({var_list})
            )
            SELECT variable, value, date, ingested_at
            FROM ranked WHERE rn = 1
        """
        from google.cloud import bigquery as bq

        job = client.query(
            sql,
            job_config=bq.QueryJobConfig(
                query_parameters=[
                    bq.ScalarQueryParameter("src", "STRING", source),
                    bq.ScalarQueryParameter("loc", "STRING", location_id),
                ]
            ),
        )
        out: list[dict[str, Any]] = []
        for r in job.result():
            var = str(r["variable"])
            try:
                val = float(r["value"]) if r["value"] is not None else None
            except (TypeError, ValueError):
                val = None
            if val is not None and var == "water_potential_kpa" and val <= _WP_SENTINEL:
                continue
            out.append(
                {
                    "variable": var,
                    "label": VAR_LABELS_PT.get(var, var),
                    "unit": VAR_UNITS.get(var, ""),
                    "value": val,
                    "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r.get("date") or ""),
                    "ingested_at": (
                        r["ingested_at"].isoformat()
                        if hasattr(r.get("ingested_at"), "isoformat")
                        else None
                    ),
                }
            )
        rank = {v: i for i, v in enumerate(CHART_VARS_PRIORITY)}
        out.sort(key=lambda x: rank.get(x["variable"], 99))
        return out
    except Exception:
        return []


def build_sensor_series_payload(
    *,
    location_id: str,
    source: str = "zentra",
    variable: str | None = None,
    family: str | None = None,
    days: int | str = 7,
    bq_dataset: str | None = None,
    lang_pt: bool = True,
) -> dict[str, Any]:
    source = (source or "zentra").strip().lower()
    if source not in ("zentra", "orion"):
        source = "zentra"
    days_i = _parse_days(days)

    if source == "orion":
        meta = load_orion_device_meta(location_id, bq_dataset=bq_dataset) or {
            "location_id": location_id,
            "name": location_id,
            "sensors": ["Orion"],
            "source": "orion",
        }
        # Orion precip-focused until geotech series lands in clima_series
        ports = [
            {
                "id": "Orion",
                "label": "Orion",
                "variables": [
                    {"id": "precip_mm", "label": VAR_LABELS_PT["precip_mm"], "unit": "mm"},
                ],
            }
        ]
    else:
        meta = load_device_meta(location_id, bq_dataset=bq_dataset) or {
            "location_id": location_id,
            "name": location_id,
            "sensors": [],
            "source": "zentra",
        }
        ports = ports_for_device(meta.get("sensors"), meta.get("n_ports"))

    avail = _available_variables(
        meta.get("location_id") or location_id,
        source,
        bq_dataset=bq_dataset,
        days=max(days_i, 30),
    )
    # Filter port variables to ones that exist (keep structure even if empty)
    if avail:
        avail_set = set(avail)
        for p in ports:
            p["variables"] = [v for v in p["variables"] if v["id"] in avail_set] or p["variables"]

    fam_id, default_var = default_variable_for_ports(ports)
    if family:
        for p in ports:
            if p["id"].lower() == family.lower() or family.lower() in p["id"].lower():
                fam_id = p["id"]
                if p.get("variables"):
                    default_var = p["variables"][0]["id"]
                break
    var = (variable or default_var or "soil_vwc").strip()
    # Ensure selected family matches variable
    for p in ports:
        if any(v["id"] == var for v in p.get("variables") or []):
            fam_id = p["id"]
            break

    loc = meta.get("location_id") or location_id
    points = fetch_series_points(
        location_id=loc,
        source=source,
        variable=var,
        days=days_i,
        bq_dataset=bq_dataset,
    )
    latest = fetch_latest_values(
        location_id=loc,
        source=source,
        bq_dataset=bq_dataset,
    )
    last_ts = None
    for cell in latest:
        if cell.get("ingested_at"):
            last_ts = cell["ingested_at"]
            break
    freshness = "—"
    if last_ts:
        try:
            ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            if age_h < 6:
                freshness = "ok"
            elif age_h < 24:
                freshness = f"{age_h:.0f} h"
            else:
                freshness = f"{age_h / 24.0:.0f} d"
        except Exception:
            freshness = "—"

    return {
        "available": bool(points) or bool(latest),
        "source": source,
        "location_id": loc,
        "name": meta.get("name") or loc,
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "status": meta.get("status") or "",
        "sensors": meta.get("sensors") or [],
        "asset_class": meta.get("asset_class") or "",
        "ports": ports,
        "family": fam_id,
        "variable": var,
        "variable_label": VAR_LABELS_PT.get(var, var),
        "unit": VAR_UNITS.get(var, ""),
        "days": days_i,
        "windows": [
            {"id": 1, "label": "24 h" if lang_pt else "24 h"},
            {"id": 7, "label": "7 d" if lang_pt else "7 d"},
            {"id": 30, "label": "30 d" if lang_pt else "30 d"},
        ],
        "points": points,
        "latest": latest,
        "freshness": freshness,
        "n_points": len(points),
    }
