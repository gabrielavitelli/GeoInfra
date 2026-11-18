"""Time-series JSON for Zentra / Orion sensors (clima_series).

Port/série selector uses sensor families from ``zentra_devices.sensors``
(TEROS / ATMOS / health) because ``clima_series`` does not yet store port_num.
Falls back to GCS snapshots when BigQuery/ADC is unavailable locally.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_WP_SENTINEL = -90000.0

_DEFAULT_DEVICES_GCS = "gs://efc-clima-data/obs/zentra/zentra_devices_latest.json"
_DEFAULT_LOCATIONS_GCS = "gs://efc-clima-data/obs/zentra/zentra_locations_latest.json"
_DEFAULT_SERIES_GCS = "gs://efc-clima-data/obs/zentra/zentra_clima_series_latest.ndjson"
_DEFAULT_READINGS_GCS = "gs://efc-clima-data/obs/zentra/zentra_readings_latest.ndjson"

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

# Plotly accent palette aligned with stitch dark (cyan / coral / amber / mint).
VAR_COLORS: dict[str, str] = {
    "soil_vwc": "#38bdf8",
    "water_potential_kpa": "#f97316",
    "precip_mm": "#67e8f9",
    "soil_temp_c": "#a78bfa",
    "temp_c": "#fbbf24",
    "rh_pct": "#34d399",
    "pressure_hpa": "#94a3b8",
    "battery_pct": "#4ade80",
    "battery_v": "#86efac",
    "signal_pct": "#60a5fa",
}

# Default multi-series overview (estacao-like panel).
MULTI_OVERVIEW_VARS: tuple[str, ...] = (
    "soil_vwc",
    "water_potential_kpa",
    "precip_mm",
)

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

MEASUREMENT_TO_VAR: dict[str, str] = {
    "water content": "soil_vwc",
    "raw vwc": "soil_vwc",
    "matric potential": "water_potential_kpa",
    "precipitation": "precip_mm",
    "soil temperature": "soil_temp_c",
    "air temperature": "temp_c",
    "relative humidity": "rh_pct",
    "atmospheric pressure": "pressure_hpa",
    "battery percent": "battery_pct",
    "battery percentage": "battery_pct",
    "battery": "battery_pct",
    "battery voltage": "battery_v",
    "signal strength": "signal_pct",
    "rssi": "signal_pct",
}

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


def _gcs_fallback_enabled() -> bool:
    return (os.environ.get("ZENTRA_SKIP_GCS_FALLBACK") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def _client():
    try:
        from gcp.bq_load_clima import _bq_client

        return _bq_client()
    except Exception as exc:
        logger.warning("sensor_series BQ client unavailable: %s", exc)
        return None


def _pfx(dataset: str) -> str:
    from gcp.bq_config import get_bq_fully_qualified_prefix

    return get_bq_fully_qualified_prefix(dataset)


def _read_uri_bytes(uri: str) -> bytes | None:
    u = (uri or "").strip()
    if not u:
        return None
    try:
        if u.startswith("gs://"):
            from google.cloud import storage

            _, _, rest = u.partition("gs://")
            bucket_name, _, blob_name = rest.partition("/")
            if not bucket_name or not blob_name:
                return None
            client = storage.Client()
            return client.bucket(bucket_name).blob(blob_name).download_as_bytes()
        if u.startswith("file://"):
            u = u[7:]
        with open(u, "rb") as fh:
            return fh.read()
    except Exception as exc:
        logger.warning("sensor_series URI read failed (%s): %s", u, exc)
        return None


def _location_id_from_device_id(device_id: str) -> str:
    did = (device_id or "").strip()
    if not did:
        return ""
    if did.startswith("zentra_"):
        return did
    return "zentra_" + did.replace("-", "_")


def _measurement_to_variable(measurement: str | None, mapped: str | None = None) -> str | None:
    if mapped and str(mapped).strip():
        return str(mapped).strip()
    key = (measurement or "").strip().lower()
    if not key:
        return None
    if key in MEASUREMENT_TO_VAR:
        return MEASUREMENT_TO_VAR[key]
    for needle, var in MEASUREMENT_TO_VAR.items():
        if needle in key:
            return var
    return None


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


def _device_meta_from_gcs(location_id: str) -> dict[str, Any] | None:
    """Resolve one device from GCS devices/locations snapshots."""
    if not location_id or not _gcs_fallback_enabled():
        return None
    try:
        from monitor_web.services.zentra_sensors import load_zentra_devices_from_gcs
    except Exception:
        load_zentra_devices_from_gcs = None  # type: ignore
    df = None
    if load_zentra_devices_from_gcs is not None:
        try:
            df = load_zentra_devices_from_gcs()
        except Exception as exc:
            logger.warning("sensor_series GCS devices meta failed: %s", exc)
    if df is None or getattr(df, "empty", True):
        return None
    loc = location_id.strip()
    alt = loc if loc.startswith("zentra_") else _location_id_from_device_id(loc)
    for _, r in df.iterrows():
        rid = str(r.get("location_id") or r.get("codEstacao") or "").strip()
        if rid not in (loc, alt):
            continue
        sensors = r.get("sensors")
        if hasattr(sensors, "tolist"):
            sensors = list(sensors.tolist())
        elif not isinstance(sensors, (list, tuple)):
            sensors = []
        try:
            lat = float(r["latitude"]) if r.get("latitude") is not None else None
            lon = float(r["longitude"]) if r.get("longitude") is not None else None
        except (TypeError, ValueError):
            lat = lon = None
        return {
            "location_id": rid,
            "name": str(r.get("nome") or rid),
            "lat": lat,
            "lon": lon,
            "sensors": [str(s) for s in sensors if s],
            "n_ports": None,
            "status": str(r.get("status") or "active"),
            "asset_class": str(r.get("asset_class") or ""),
            "model": str(r.get("model") or ""),
            "source": "zentra",
            "last_measurement": r.get("last_measurement"),
        }
    return None


def load_device_meta(location_id: str, *, bq_dataset: str | None = None) -> dict[str, Any] | None:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if not location_id:
        return None
    if client is not None:
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
            if rows:
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
            logger.warning("sensor_series BQ device meta empty for %s; trying GCS", location_id)
        except Exception as exc:
            logger.warning("sensor_series BQ device meta failed: %s; trying GCS", exc)
    return _device_meta_from_gcs(location_id)


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


def _available_variables_from_gcs(location_id: str) -> list[str]:
    path = (os.environ.get("ZENTRA_SERIES_GCS_URI") or _DEFAULT_SERIES_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        # Try readings snapshot for denser short-window charts.
        return _available_variables_from_readings(location_id)
    loc = location_id.strip()
    found: set[str] = set()
    try:
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("location_id") or "").strip() != loc:
                continue
            var = str(row.get("variable") or "").strip()
            if var:
                found.add(var)
    except Exception as exc:
        logger.warning("sensor_series GCS avail vars failed: %s", exc)
    if not found:
        found.update(_available_variables_from_readings(location_id))
    rank = {v: i for i, v in enumerate(CHART_VARS_PRIORITY)}
    return sorted(found, key=lambda v: (rank.get(v, 99), v))


def _available_variables_from_readings(location_id: str) -> list[str]:
    path = (os.environ.get("ZENTRA_READINGS_GCS_URI") or _DEFAULT_READINGS_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        return []
    loc = location_id.strip()
    found: set[str] = set()
    try:
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("location_id") or "").strip() != loc:
                continue
            var = _measurement_to_variable(row.get("measurement"), row.get("mapped_variable"))
            if var:
                found.add(var)
    except Exception as exc:
        logger.warning("sensor_series GCS readings avail failed: %s", exc)
    rank = {v: i for i, v in enumerate(CHART_VARS_PRIORITY)}
    return sorted(found, key=lambda v: (rank.get(v, 99), v))


def _available_variables(
    location_id: str,
    source: str,
    *,
    bq_dataset: str | None = None,
    days: int = 30,
) -> list[str]:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if client is not None:
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
            if vars_:
                rank = {v: i for i, v in enumerate(CHART_VARS_PRIORITY)}
                vars_.sort(key=lambda v: (rank.get(v, 99), v))
                return vars_
            logger.warning("sensor_series BQ avail vars empty; trying GCS")
        except Exception as exc:
            logger.warning("sensor_series BQ avail vars failed: %s; trying GCS", exc)
    if source == "zentra":
        return _available_variables_from_gcs(location_id)
    return []


def _points_from_gcs_series(
    *,
    location_id: str,
    variable: str,
    days: int,
    limit: int = 2500,
) -> list[dict[str, Any]]:
    if not _gcs_fallback_enabled():
        return []
    path = (os.environ.get("ZENTRA_SERIES_GCS_URI") or _DEFAULT_SERIES_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        return []
    start = date.today() - timedelta(days=max(days, 1))
    loc = location_id.strip()
    # date → [values]
    buckets: dict[str, list[float]] = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("location_id") or "").strip() != loc:
                continue
            if str(row.get("variable") or "") != variable:
                continue
            d_raw = row.get("date")
            if d_raw is None:
                continue
            d_str = str(d_raw)[:10]
            try:
                d_obj = date.fromisoformat(d_str)
            except ValueError:
                continue
            if d_obj < start:
                continue
            try:
                fv = float(row["value"]) if row.get("value") is not None else None
            except (TypeError, ValueError):
                fv = None
            if fv is None or not math.isfinite(fv):
                continue
            if variable == "water_potential_kpa" and fv <= _WP_SENTINEL:
                continue
            buckets.setdefault(d_str, []).append(fv)
    except Exception as exc:
        logger.warning("sensor_series GCS series points failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for d_str in sorted(buckets.keys())[:limit]:
        vals = buckets[d_str]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        d_obj = date.fromisoformat(d_str)
        ts = datetime(d_obj.year, d_obj.month, d_obj.day, 12, 0, tzinfo=timezone.utc).isoformat()
        out.append({"t": ts, "v": mean, "n": len(vals)})
    return out


def _points_from_gcs_readings(
    *,
    location_id: str,
    variable: str,
    days: int,
    port_num: int | None = None,
    limit: int = 2500,
) -> list[dict[str, Any]]:
    """Dense timestamped points from ``zentra_readings_latest.ndjson`` (per-port)."""
    if not _gcs_fallback_enabled():
        return []
    path = (os.environ.get("ZENTRA_READINGS_GCS_URI") or _DEFAULT_READINGS_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        return []
    loc = location_id.strip()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    out: list[dict[str, Any]] = []
    try:
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("location_id") or "").strip() != loc:
                continue
            var = _measurement_to_variable(row.get("measurement"), row.get("mapped_variable"))
            if var != variable:
                continue
            if port_num is not None:
                try:
                    if int(row.get("port_num")) != int(port_num):
                        continue
                except (TypeError, ValueError):
                    continue
            try:
                fv = float(row["value"]) if row.get("value") is not None else None
            except (TypeError, ValueError):
                fv = None
            if fv is None or not math.isfinite(fv):
                continue
            if variable == "water_potential_kpa" and fv <= _WP_SENTINEL:
                continue
            ts_raw = row.get("datetime") or row.get("timestamp")
            ts_iso = None
            if isinstance(ts_raw, (int, float)):
                try:
                    ts_iso = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    ts_iso = None
            elif ts_raw:
                try:
                    ts_iso = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).astimezone(
                        timezone.utc
                    ).isoformat()
                except ValueError:
                    ts_iso = str(ts_raw)
            if not ts_iso:
                continue
            try:
                ts_dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
                if ts_dt < cutoff:
                    continue
            except ValueError:
                pass
            out.append(
                {
                    "t": ts_iso,
                    "v": fv,
                    "n": 1,
                    "port": row.get("port_num"),
                    "sensor": row.get("sensor_name"),
                }
            )
    except Exception as exc:
        logger.warning("sensor_series GCS readings points failed: %s", exc)
        return []
    out.sort(key=lambda p: p["t"])
    return out[:limit]


def fetch_series_points(
    *,
    location_id: str,
    source: str = "zentra",
    variable: str = "soil_vwc",
    days: int = 7,
    bq_dataset: str | None = None,
    limit: int = 2500,
    port_num: int | None = None,
) -> list[dict[str, Any]]:
    """Series from clima_series (daily mean) or GCS readings (dense timestamps).

    Prefer BigQuery daily means for multi-day windows. For short windows, try
    GCS readings (port-aware) when available, then GCS clima_series snapshot.
    """
    if not location_id or not variable:
        return []
    days = _parse_days(days)
    ds = _bq_dataset(bq_dataset)
    client = _client()
    start = date.today() - timedelta(days=max(days, 1))

    # Dense readings first for 24h (and as enrichment when BQ empty).
    if source == "zentra" and days <= 1:
        dense = _points_from_gcs_readings(
            location_id=location_id,
            variable=variable,
            days=days,
            port_num=port_num,
            limit=limit,
        )
        if dense:
            return dense

    if client is not None:
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
            if out:
                return out
            logger.warning("sensor_series BQ points empty; trying GCS")
        except Exception as exc:
            logger.warning("sensor_series BQ points failed: %s; trying GCS", exc)

    if source != "zentra":
        return []
    dense = _points_from_gcs_readings(
        location_id=location_id,
        variable=variable,
        days=days,
        port_num=port_num,
        limit=limit,
    )
    if dense:
        return dense
    return _points_from_gcs_series(
        location_id=location_id,
        variable=variable,
        days=days,
        limit=limit,
    )


def _latest_from_gcs(location_id: str, variables: list[str]) -> list[dict[str, Any]]:
    try:
        from monitor_web.services.zentra_sensors import load_zentra_latest_from_gcs
    except Exception:
        load_zentra_latest_from_gcs = None  # type: ignore
    bucket: dict[str, Any] = {}
    if load_zentra_latest_from_gcs is not None:
        try:
            all_latest = load_zentra_latest_from_gcs(variables=tuple(variables))
            bucket = all_latest.get(location_id) or {}
        except Exception as exc:
            logger.warning("sensor_series GCS latest failed: %s", exc)
    out: list[dict[str, Any]] = []
    for var in variables:
        cell = bucket.get(var)
        if not cell:
            continue
        val = cell.get("value")
        if val is not None and var == "water_potential_kpa" and float(val) <= _WP_SENTINEL:
            continue
        d = cell.get("date")
        out.append(
            {
                "variable": var,
                "label": VAR_LABELS_PT.get(var, var),
                "unit": VAR_UNITS.get(var, ""),
                "value": val,
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d or ""),
                "ingested_at": None,
            }
        )
    return out


def fetch_latest_values(
    *,
    location_id: str,
    source: str = "zentra",
    variables: list[str] | None = None,
    bq_dataset: str | None = None,
) -> list[dict[str, Any]]:
    ds = _bq_dataset(bq_dataset)
    client = _client()
    vars_ = variables or list(CHART_VARS_PRIORITY)
    if client is not None:
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
            if out:
                rank = {v: i for i, v in enumerate(CHART_VARS_PRIORITY)}
                out.sort(key=lambda x: rank.get(x["variable"], 99))
                return out
            logger.warning("sensor_series BQ latest empty; trying GCS")
        except Exception as exc:
            logger.warning("sensor_series BQ latest failed: %s; trying GCS", exc)
    if source == "zentra":
        return _latest_from_gcs(location_id, vars_)
    return []


def _trace_axis(variable: str) -> str:
    """Primary (y) for soil moisture/potential; secondary (y2) for precip/meteo."""
    if variable in ("precip_mm", "temp_c", "rh_pct", "pressure_hpa"):
        return "y2"
    return "y"


def _build_traces(
    *,
    location_id: str,
    source: str,
    variables: list[str],
    days: int,
    bq_dataset: str | None,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for var in variables:
        pts = fetch_series_points(
            location_id=location_id,
            source=source,
            variable=var,
            days=days,
            bq_dataset=bq_dataset,
        )
        if not pts:
            continue
        traces.append(
            {
                "variable": var,
                "label": VAR_LABELS_PT.get(var, var),
                "unit": VAR_UNITS.get(var, ""),
                "color": VAR_COLORS.get(var, "#38bdf8"),
                "axis": _trace_axis(var),
                "points": pts,
                "n_points": len(pts),
            }
        )
    return traces


def build_sensor_series_payload(
    *,
    location_id: str,
    source: str = "zentra",
    variable: str | None = None,
    family: str | None = None,
    days: int | str = 7,
    bq_dataset: str | None = None,
    lang_pt: bool = True,
    multi: bool | None = None,
    variables: list[str] | None = None,
) -> dict[str, Any]:
    source = (source or "zentra").strip().lower()
    if source not in ("zentra", "orion"):
        source = "zentra"
    days_i = _parse_days(days)
    # Default: multi-series overview (VWC + pot + precip) unless a single var forced.
    want_multi = True if multi is None else bool(multi)
    if variable and multi is None and not variables:
        # Explicit single-variable request keeps single mode for back-compat.
        want_multi = False

    if source == "orion":
        meta = load_orion_device_meta(location_id, bq_dataset=bq_dataset) or {
            "location_id": location_id,
            "name": location_id,
            "sensors": ["Orion"],
            "source": "orion",
        }
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

    # Overview chip first (multi-series).
    overview_label = "Visão" if lang_pt else "Overview"
    port_chips = [{"id": "__overview__", "label": overview_label}] + [
        {"id": p["id"], "label": p["label"]} for p in ports
    ]

    avail = _available_variables(
        meta.get("location_id") or location_id,
        source,
        bq_dataset=bq_dataset,
        days=max(days_i, 30),
    )
    if avail:
        avail_set = set(avail)
        for p in ports:
            filtered = [v for v in p["variables"] if v["id"] in avail_set]
            if filtered:
                p["variables"] = filtered

    fam_id, default_var = default_variable_for_ports(ports)
    family_raw = (family or "").strip()
    if family_raw in ("__overview__", "overview", "all", "visão", "visao"):
        fam_id = "__overview__"
        want_multi = True
    elif family_raw:
        for p in ports:
            if p["id"].lower() == family_raw.lower() or family_raw.lower() in p["id"].lower():
                fam_id = p["id"]
                if p.get("variables"):
                    default_var = p["variables"][0]["id"]
                break

    var = (variable or default_var or "soil_vwc").strip()
    if fam_id != "__overview__":
        for p in ports:
            if any(v["id"] == var for v in p.get("variables") or []):
                fam_id = p["id"]
                break

    loc = meta.get("location_id") or location_id

    # Resolve which variables to plot.
    active_vars: list[str]
    if variables:
        active_vars = [v for v in variables if v]
    elif want_multi and fam_id == "__overview__":
        active_vars = [v for v in MULTI_OVERVIEW_VARS if (not avail or v in avail)]
        if not active_vars:
            active_vars = list(avail[:3]) if avail else [var]
    elif want_multi and fam_id != "__overview__":
        fam = next((p for p in ports if p["id"] == fam_id), None)
        active_vars = [v["id"] for v in (fam.get("variables") if fam else []) or []]
        if not active_vars:
            active_vars = [var]
    else:
        active_vars = [var]

    traces = _build_traces(
        location_id=loc,
        source=source,
        variables=active_vars,
        days=days_i,
        bq_dataset=bq_dataset,
    )
    # Primary points = first trace (back-compat for older JS).
    points = traces[0]["points"] if traces else fetch_series_points(
        location_id=loc,
        source=source,
        variable=var,
        days=days_i,
        bq_dataset=bq_dataset,
    )
    if traces:
        var = traces[0]["variable"]

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
        if cell.get("date") and last_ts is None:
            last_ts = cell.get("date")
    freshness = "—"
    if last_ts:
        try:
            ts = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
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
        "available": bool(traces) or bool(points) or bool(latest),
        "source": source,
        "location_id": loc,
        "name": meta.get("name") or loc,
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "status": meta.get("status") or "",
        "sensors": meta.get("sensors") or [],
        "asset_class": meta.get("asset_class") or "",
        "ports": ports,
        "port_chips": port_chips,
        "family": fam_id,
        "variable": var,
        "variable_label": VAR_LABELS_PT.get(var, var),
        "unit": VAR_UNITS.get(var, ""),
        "active_variables": active_vars,
        "mode": "multi" if len(traces) > 1 or want_multi else "single",
        "traces": traces,
        "days": days_i,
        "windows": [
            {"id": 1, "label": "24h"},
            {"id": 7, "label": "7d"},
            {"id": 30, "label": "30d"},
        ],
        "points": points,
        "latest": latest,
        "freshness": freshness,
        "n_points": sum(t.get("n_points") or 0 for t in traces) if traces else len(points),
        "resolution": "hourly" if days_i <= 1 and any(
            (t.get("points") or [{}])[0].get("n") == 1 for t in traces
        ) else "daily",
    }
