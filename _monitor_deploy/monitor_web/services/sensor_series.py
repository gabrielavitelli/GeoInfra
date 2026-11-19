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
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ_SP = ZoneInfo("America/Sao_Paulo")

# Distinct stroke colors when one variable spans multiple TEROS ports/depths.
PORT_COLORS: tuple[str, ...] = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
)

_WP_SENTINEL = -90000.0

_DEFAULT_DEVICES_GCS = "gs://efc-clima-data/obs/zentra/zentra_devices_latest.json"
_DEFAULT_LOCATIONS_GCS = "gs://efc-clima-data/obs/zentra/zentra_locations_latest.json"
_DEFAULT_SERIES_GCS = "gs://efc-clima-data/obs/zentra/zentra_clima_series_latest.ndjson"
_DEFAULT_READINGS_GCS = "gs://efc-clima-data/obs/zentra/zentra_readings_latest.ndjson"

VAR_LABELS_PT: dict[str, str] = {
    "soil_vwc": "VWC: solo",
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

# Logger health — plotted together on a de-emphasized gray card.
HEALTH_VARS: tuple[str, ...] = ("battery_pct", "signal_pct")

# Chart card titles (reference-style long labels).
VAR_CHART_TITLES_PT: dict[str, str] = {
    "soil_vwc": "Umidade do solo (VWC)",
    "water_potential_kpa": "Potencial de água no solo",
    "soil_temp_c": "Temperatura do solo",
    "precip_mm": "Precipitação",
    "temp_c": "Temperatura do ar",
    "rh_pct": "Umidade relativa",
    "pressure_hpa": "Pressão atmosférica",
    "battery_pct": "Bateria",
    "battery_v": "Tensão da bateria",
    "signal_pct": "Sinal",
}

VAR_CHART_TITLES_EN: dict[str, str] = {
    "soil_vwc": "Soil moisture (VWC)",
    "water_potential_kpa": "Soil water potential",
    "soil_temp_c": "Soil temperature",
    "precip_mm": "Precipitation",
    "temp_c": "Air temperature",
    "rh_pct": "Relative humidity",
    "pressure_hpa": "Atmospheric pressure",
    "battery_pct": "Battery",
    "battery_v": "Battery voltage",
    "signal_pct": "Signal",
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

# Plotly default-ish palette for light cream card gallery.
VAR_COLORS: dict[str, str] = {
    "soil_vwc": "#1f77b4",
    "water_potential_kpa": "#ff7f0e",
    "precip_mm": "#2ca02c",
    "soil_temp_c": "#9467bd",
    "temp_c": "#d62728",
    "rh_pct": "#17becf",
    "pressure_hpa": "#7f7f7f",
    "battery_pct": "#bcbd22",
    "battery_v": "#8c564b",
    "signal_pct": "#e377c2",
}

# Default multi-series overview (gallery cards — prefer soil + precip + health).
MULTI_OVERVIEW_VARS: tuple[str, ...] = (
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
    # Do NOT map "Raw VWC" → soil_vwc (different unit / scale; pollutes VWC charts).
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
    "signal": "signal_pct",
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


# In-process cache for large GCS snapshots (7d readings ~50MB) so gallery
# multi-variable requests do not re-download on every variable.
_URI_BYTES_CACHE: dict[str, tuple[float, bytes]] = {}
_URI_BYTES_TTL_S = float(os.environ.get("ZENTRA_GCS_BYTES_TTL_S") or "90")


def _read_uri_bytes(uri: str) -> bytes | None:
    u = (uri or "").strip()
    if not u:
        return None
    now = time.time()
    cached = _URI_BYTES_CACHE.get(u)
    if cached and (now - cached[0]) < _URI_BYTES_TTL_S:
        return cached[1]
    try:
        if u.startswith("gs://"):
            from google.cloud import storage

            _, _, rest = u.partition("gs://")
            bucket_name, _, blob_name = rest.partition("/")
            if not bucket_name or not blob_name:
                return None
            client = storage.Client()
            raw = client.bucket(bucket_name).blob(blob_name).download_as_bytes()
        else:
            path = u[7:] if u.startswith("file://") else u
            with open(path, "rb") as fh:
                raw = fh.read()
        if raw is not None:
            _URI_BYTES_CACHE[u] = (now, raw)
            # Keep cache bounded (latest readings + series + devices at most).
            if len(_URI_BYTES_CACHE) > 6:
                oldest = sorted(_URI_BYTES_CACHE.items(), key=lambda kv: kv[1][0])
                for k, _ in oldest[: max(0, len(_URI_BYTES_CACHE) - 4)]:
                    _URI_BYTES_CACHE.pop(k, None)
        return raw
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


def latest_port_readings_by_location(
    *,
    variables: tuple[str, ...] = ("soil_vwc", "water_potential_kpa"),
) -> dict[str, list[dict[str, Any]]]:
    """Latest reading per (location_id, port_num, variable) from GCS readings.

    Used by Sensores landing to show mean VWC on the ponto card and expandable
    per-port / per-depth children. Returns::

        location_id → [{port, sensor, variable, value, datetime}, ...]
    """
    if not _gcs_fallback_enabled():
        return {}
    path = (os.environ.get("ZENTRA_READINGS_GCS_URI") or _DEFAULT_READINGS_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        return {}
    want = set(variables)
    # key: (loc, port, var) → (ts_iso, value, sensor)
    best: dict[tuple[str, int, str], tuple[str, float, str]] = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            loc = str(row.get("location_id") or "").strip()
            if not loc:
                continue
            var = _measurement_to_variable(row.get("measurement"), row.get("mapped_variable"))
            if not var or var not in want:
                continue
            try:
                port = int(row.get("port_num"))
            except (TypeError, ValueError):
                continue
            try:
                fv = float(row["value"]) if row.get("value") is not None else None
            except (TypeError, ValueError):
                fv = None
            if fv is None or not math.isfinite(fv):
                continue
            if var == "water_potential_kpa" and fv <= _WP_SENTINEL:
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
                    ts_iso = (
                        datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                        .astimezone(timezone.utc)
                        .isoformat()
                    )
                except ValueError:
                    ts_iso = str(ts_raw)
            if not ts_iso:
                continue
            key = (loc, port, var)
            prev = best.get(key)
            if prev is None or ts_iso >= prev[0]:
                best[key] = (ts_iso, fv, str(row.get("sensor_name") or "").strip())
    except Exception as exc:
        logger.warning("latest_port_readings_by_location failed: %s", exc)
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for (loc, port, var), (ts_iso, fv, sensor) in best.items():
        out.setdefault(loc, []).append(
            {
                "port": port,
                "sensor": sensor,
                "variable": var,
                "value": fv,
                "datetime": ts_iso,
            }
        )
    for loc in out:
        out[loc].sort(key=lambda r: (r.get("variable") or "", r.get("port") or 0))
    return out


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
        d = int(raw) if raw is not None else 30
    except (TypeError, ValueError):
        d = 30
    if d <= 1:
        return 1
    if d <= 7:
        return 7
    if d <= 14:
        return 14
    if d <= 30:
        return 30
    return min(d, 90)


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def _freshness_from_ts(ts: datetime | None, *, lang_pt: bool = True) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_TZ_SP)
    now = datetime.now(_TZ_SP)
    age_s = (now - ts.astimezone(_TZ_SP)).total_seconds()
    if age_s < 0:
        age_s = 0.0
    age_m = age_s / 60.0
    age_h = age_s / 3600.0
    if age_m < 60:
        n = max(1, int(round(age_m))) if age_m >= 1 else 0
        if n <= 0:
            return "agora" if lang_pt else "now"
        return f"há {n} min" if lang_pt else f"{n}m ago"
    if age_h < 24:
        n = max(1, int(round(age_h)))
        return f"há {n} h" if lang_pt else f"{n}h ago"
    n = max(1, int(round(age_h / 24.0)))
    return f"há {n} d" if lang_pt else f"{n}d ago"


def _span_days(points: list[dict[str, Any]]) -> float:
    """Calendar span covered by point timestamps (days)."""
    ts_list: list[datetime] = []
    for p in points or []:
        raw = p.get("t")
        if not raw:
            continue
        try:
            ts_list.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(ts_list) < 2:
        return 0.0
    return (max(ts_list) - min(ts_list)).total_seconds() / 86400.0


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
    """Raw clima_series snapshot points (no daily mean / downsample)."""
    if not _gcs_fallback_enabled():
        return []
    path = (os.environ.get("ZENTRA_SERIES_GCS_URI") or _DEFAULT_SERIES_GCS).strip()
    raw = _read_uri_bytes(path)
    if not raw:
        return []
    start = date.today() - timedelta(days=max(days, 1))
    loc = location_id.strip()
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
            # Prefer ingested_at / valid_time when present; else noon UTC on date.
            ts_iso = None
            for key in ("valid_time", "ingested_at", "timestamp", "datetime"):
                raw_ts = row.get(key)
                if raw_ts is None or raw_ts == "":
                    continue
                try:
                    if isinstance(raw_ts, (int, float)):
                        ts_iso = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc).isoformat()
                    else:
                        ts_iso = datetime.fromisoformat(
                            str(raw_ts).replace("Z", "+00:00")
                        ).astimezone(timezone.utc).isoformat()
                    break
                except (OverflowError, OSError, ValueError, TypeError):
                    continue
            if not ts_iso:
                ts_iso = datetime(
                    d_obj.year, d_obj.month, d_obj.day, 12, 0, tzinfo=timezone.utc
                ).isoformat()
            out.append({"t": ts_iso, "v": fv, "n": 1})
    except Exception as exc:
        logger.warning("sensor_series GCS series points failed: %s", exc)
        return []
    out.sort(key=lambda p: p["t"])
    return out[:limit]


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


def _points_from_bq_readings_staging(
    *,
    location_id: str,
    variable: str,
    start: date,
    end: date,
    bq_dataset: str | None = None,
    port_num: int | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Per-port points from ``zentra_readings_staging`` (avoids clima_series sawtooth)."""
    client = _client()
    if client is None:
        return []
    ds = _bq_dataset(bq_dataset)
    pfx = _pfx(ds)
    try:
        from google.cloud import bigquery as bq

        sql = f"""
            SELECT
              COALESCE(datetime, TIMESTAMP_SECONDS(CAST(timestamp AS INT64))) AS t,
              value AS value,
              port_num AS port,
              sensor_name AS sensor
            FROM `{pfx}.zentra_readings_staging`
            WHERE location_id = @loc
              AND mapped_variable = @var
              AND value IS NOT NULL
              AND COALESCE(datetime, TIMESTAMP_SECONDS(CAST(timestamp AS INT64))) IS NOT NULL
              AND DATE(COALESCE(datetime, TIMESTAMP_SECONDS(CAST(timestamp AS INT64))),
                       'America/Sao_Paulo') BETWEEN @start AND @end
              AND (@port IS NULL OR port_num = @port)
              AND (@var != 'water_potential_kpa' OR value > {_WP_SENTINEL})
            ORDER BY t, port_num
            LIMIT @lim
        """
        job = client.query(
            sql,
            job_config=bq.QueryJobConfig(
                query_parameters=[
                    bq.ScalarQueryParameter("loc", "STRING", location_id),
                    bq.ScalarQueryParameter("var", "STRING", variable),
                    bq.ScalarQueryParameter("start", "DATE", start),
                    bq.ScalarQueryParameter("end", "DATE", end),
                    bq.ScalarQueryParameter(
                        "port", "INT64", int(port_num) if port_num is not None else None
                    ),
                    bq.ScalarQueryParameter("lim", "INT64", int(limit)),
                ]
            ),
        )
        out: list[dict[str, Any]] = []
        for r in job.result():
            t = r.get("t")
            v = r.get("value")
            if v is None or t is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            ts = t.isoformat() if hasattr(t, "isoformat") else str(t)
            out.append(
                {
                    "t": ts,
                    "v": fv,
                    "n": 1,
                    "port": r.get("port"),
                    "sensor": r.get("sensor"),
                }
            )
        return out
    except Exception as exc:
        logger.info("sensor_series BQ readings_staging unavailable: %s", exc)
        return []


def _dedupe_points_one_per_timestamp(
    points: list[dict[str, Any]],
    *,
    prefer: str = "last",
) -> list[dict[str, Any]]:
    """Exactly one Y per timestamp (keeps last or mean). Prevents Plotly sawtooth."""
    if not points:
        return []
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for p in points:
        t = str(p.get("t") or "")
        if not t:
            continue
        if t not in buckets:
            buckets[t] = []
            order.append(t)
        buckets[t].append(p)
    out: list[dict[str, Any]] = []
    for t in order:
        group = buckets[t]
        if len(group) == 1:
            out.append(group[0])
            continue
        vals: list[float] = []
        for g in group:
            try:
                vals.append(float(g["v"]))
            except (TypeError, ValueError, KeyError):
                continue
        if not vals:
            continue
        base = dict(group[-1] if prefer == "last" else group[0])
        if prefer == "mean":
            base["v"] = sum(vals) / len(vals)
            base["n"] = len(vals)
            base["is_average"] = True
        else:
            base["v"] = vals[-1]
            base["n"] = 1
        out.append(base)
    return out


def fetch_series_points(
    *,
    location_id: str,
    source: str = "zentra",
    variable: str = "soil_vwc",
    days: int = 30,
    bq_dataset: str | None = None,
    limit: int = 5000,
    port_num: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Raw measurement points — no daily/hourly mean aggregation.

    Prefer GCS readings (true ``datetime`` + ``port_num``) for Zentra when they
    cover the requested window. Else BQ ``zentra_readings_staging`` (per-port).
    Portless ``clima_series`` is last resort — it sawtooth-merges TEROS depths.
    """
    if not location_id or not variable:
        return []
    days = _parse_days(days)
    ds = _bq_dataset(bq_dataset)
    client = _client()
    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=max(days, 1))
    if start > end:
        start, end = end, start
    # Effective lookback for GCS cutoff helpers that take ``days``.
    days_eff = max(days, (end - start).days + 1)

    dense: list[dict[str, Any]] = []
    if source == "zentra":
        dense = _points_from_gcs_readings(
            location_id=location_id,
            variable=variable,
            days=days_eff,
            port_num=port_num,
            limit=limit,
        )
        # Filter to [start, end] inclusive (Brazil calendar days).
        if dense and (start or end):
            filtered: list[dict[str, Any]] = []
            for p in dense:
                try:
                    ts = datetime.fromisoformat(str(p["t"]).replace("Z", "+00:00"))
                    d_local = ts.astimezone(_TZ_SP).date()
                except Exception:
                    filtered.append(p)
                    continue
                if start and d_local < start:
                    continue
                if end and d_local > end:
                    continue
                filtered.append(p)
            dense = filtered
        # Prefer dense readings when they encode port_num (TEROS depths) —
        # BQ clima_series has no port and would either sawtooth-merge or drop
        # depth traces. Also keep dense when it spans most of the window.
        span = _span_days(dense)
        want = float(max((end - start).days, 1))
        has_ports = any(p.get("port") is not None and p.get("port") != "" for p in dense)
        if dense and (
            has_ports
            or port_num is not None
            or span >= min(want * 0.45, want - 0.5)
            or want <= 3
        ):
            return dense

        # BQ staging has port_num + true datetime (fixes z6-35950 when GCS
        # snapshot omits the device).
        staged = _points_from_bq_readings_staging(
            location_id=location_id,
            variable=variable,
            start=start,
            end=end,
            bq_dataset=bq_dataset,
            port_num=port_num,
            limit=limit,
        )
        if staged:
            return staged

    bq_out: list[dict[str, Any]] = []
    if client is not None:
        try:
            pfx = _pfx(ds)
            from google.cloud import bigquery as bq

            sql = f"""
                SELECT
                  COALESCE(valid_time, ingested_at, TIMESTAMP(date)) AS t,
                  value AS value
                FROM `{pfx}.clima_series`
                WHERE source = @src
                  AND location_id = @loc
                  AND variable = @var
                  AND date >= @start
                  AND date <= @end
                  AND value IS NOT NULL
                  AND (@var != 'water_potential_kpa' OR value > {_WP_SENTINEL})
                ORDER BY t
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
                        bq.ScalarQueryParameter("end", "DATE", end),
                        bq.ScalarQueryParameter("lim", "INT64", int(limit)),
                    ]
                ),
            )
            for r in job.result():
                t = r.get("t")
                v = r.get("value")
                if v is None or t is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv):
                    continue
                ts = t.isoformat() if hasattr(t, "isoformat") else str(t)
                bq_out.append({"t": ts, "v": fv, "n": 1})
            if bq_out and (not dense or _span_days(bq_out) >= _span_days(dense)):
                return bq_out
            if dense:
                return dense
            if bq_out:
                return bq_out
            logger.warning("sensor_series BQ points empty; trying GCS")
        except Exception as exc:
            logger.warning("sensor_series BQ points failed: %s; trying GCS", exc)
            if dense:
                return dense

    if dense:
        return dense
    if source != "zentra":
        return []
    return _points_from_gcs_series(
        location_id=location_id,
        variable=variable,
        days=days_eff,
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


def _port_key(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    try:
        return str(int(raw))
    except (TypeError, ValueError):
        return str(raw).strip() or None


def _traces_for_variable(
    *,
    variable: str,
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One Plotly trace per port when multiple ports share a timestamp (no sawtooth)."""
    if not points:
        return []
    base_label = VAR_LABELS_PT.get(variable, variable)
    chart_title = VAR_CHART_TITLES_PT.get(variable, base_label)
    unit = VAR_UNITS.get(variable, "")
    by_port: dict[str, list[dict[str, Any]]] = {}
    no_port: list[dict[str, Any]] = []
    sensor_by_port: dict[str, str] = {}
    for p in points:
        pk = _port_key(p.get("port"))
        if pk is None:
            no_port.append(p)
            continue
        by_port.setdefault(pk, []).append(p)
        sn = p.get("sensor")
        if sn and pk not in sensor_by_port:
            sensor_by_port[pk] = str(sn)

    def _pack(
        *,
        pts: list[dict[str, Any]],
        port: str | None,
        label: str,
        color: str,
        mean_if_dupes: bool = False,
    ) -> dict[str, Any]:
        clean = _dedupe_points_one_per_timestamp(
            pts, prefer="mean" if mean_if_dupes else "last"
        )
        title = chart_title
        out_label = label
        if mean_if_dupes and any(p.get("is_average") for p in clean):
            if variable == "soil_vwc":
                out_label = "VWC médio" if "médio" not in label.lower() else label
                title = "Umidade do solo (VWC médio)"
        return {
            "variable": variable,
            "port": port,
            "label": out_label,
            "chart_title": title,
            "unit": unit,
            "color": color,
            "axis": "y",
            "points": clean,
            "n_points": len(clean),
        }

    # Single undifferentiated series — keep one trace.
    if len(by_port) <= 1 and not (by_port and no_port):
        only_port = next(iter(by_port.keys()), None) if by_port else None
        pts = by_port[only_port] if only_port is not None else no_port or points
        label = base_label
        if only_port is not None:
            sn = sensor_by_port.get(only_port)
            label = f"{base_label} · Porta {only_port}" + (f" ({sn})" if sn else "")
        # Portless multi-value timestamps (BQ clima_series) → mean, not zigzag.
        mean_dupes = only_port is None
        return [
            _pack(
                pts=pts,
                port=only_port,
                label=label,
                color=VAR_COLORS.get(variable, "#1f77b4"),
                mean_if_dupes=mean_dupes,
            )
        ]
    traces: list[dict[str, Any]] = []
    for i, pk in enumerate(sorted(by_port.keys(), key=lambda x: (len(x), x))):
        pts = by_port[pk]
        sn = sensor_by_port.get(pk)
        label = f"{base_label} · Porta {pk}" + (f" ({sn})" if sn else "")
        traces.append(
            _pack(
                pts=pts,
                port=pk,
                label=label,
                color=PORT_COLORS[i % len(PORT_COLORS)],
            )
        )
    if no_port:
        traces.append(
            _pack(
                pts=no_port,
                port=None,
                label=f"{base_label} · —",
                color=VAR_COLORS.get(variable, "#7f7f7f"),
                mean_if_dupes=True,
            )
        )
    return traces


def _build_traces(
    *,
    location_id: str,
    source: str,
    variables: list[str],
    days: int,
    bq_dataset: str | None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for var in variables:
        pts = fetch_series_points(
            location_id=location_id,
            source=source,
            variable=var,
            days=days,
            bq_dataset=bq_dataset,
            start=start,
            end=end,
        )
        if not pts:
            continue
        traces.extend(_traces_for_variable(variable=var, points=pts))
    return traces


def build_sensor_series_payload(
    *,
    location_id: str,
    source: str = "zentra",
    variable: str | None = None,
    family: str | None = None,
    days: int | str = 30,
    bq_dataset: str | None = None,
    lang_pt: bool = True,
    multi: bool | None = None,
    variables: list[str] | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
) -> dict[str, Any]:
    source = (source or "zentra").strip().lower()
    if source not in ("zentra", "orion"):
        source = "zentra"
    days_i = _parse_days(days)
    start_d = start if isinstance(start, date) else _parse_iso_date(str(start) if start else None)
    end_d = end if isinstance(end, date) else _parse_iso_date(str(end) if end else None)
    if end_d is None:
        end_d = date.today()
    if start_d is None:
        start_d = end_d - timedelta(days=max(days_i, 1))
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    days_i = max(days_i, (end_d - start_d).days + 1)
    days_i = _parse_days(days_i)
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
        # Gallery: env cards (priority, capped) + always reserve health pair.
        ranked = [v for v in CHART_VARS_PRIORITY if (not avail or v in avail)]
        if not ranked:
            ranked = [v for v in MULTI_OVERVIEW_VARS if (not avail or v in avail)]
        # All mapped env vars (no artificial cap) — one Plotly card per
        # (variable × port) in the gallery; health pair on the gray card.
        env_ranked = [v for v in ranked if v not in HEALTH_VARS]
        active_vars = list(env_ranked) if env_ranked else ([var] if var else [])
        # Always attempt health pair; empty traces are dropped in _build_traces.
        for hv in HEALTH_VARS:
            if hv not in active_vars:
                active_vars.append(hv)
        if avail and "battery_v" in avail and "battery_v" not in active_vars:
            active_vars.append("battery_v")
        if not active_vars:
            active_vars = list(avail) if avail else [var]
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
        start=start_d,
        end=end_d,
    )
    # Primary points = first trace (back-compat for older JS).
    points = traces[0]["points"] if traces else fetch_series_points(
        location_id=loc,
        source=source,
        variable=var,
        days=days_i,
        bq_dataset=bq_dataset,
        start=start_d,
        end=end_d,
    )
    if traces:
        var = traces[0]["variable"]

    latest = fetch_latest_values(
        location_id=loc,
        source=source,
        bq_dataset=bq_dataset,
    )
    # Prefer device last_measurement, then max raw point time, then latest cells.
    last_dt: datetime | None = None
    lm_meta = meta.get("last_measurement")
    try:
        lm_i = int(lm_meta) if lm_meta is not None and str(lm_meta).strip() != "" else None
    except (TypeError, ValueError):
        lm_i = None
    if lm_i:
        last_dt = datetime.fromtimestamp(lm_i, tz=timezone.utc)
    if last_dt is None:
        for tr in traces:
            for p in tr.get("points") or []:
                try:
                    cand = datetime.fromisoformat(str(p.get("t")).replace("Z", "+00:00"))
                except Exception:
                    continue
                if last_dt is None or cand > last_dt:
                    last_dt = cand
    if last_dt is None:
        for cell in latest:
            raw = cell.get("ingested_at") or cell.get("date")
            if not raw:
                continue
            try:
                s = str(raw)
                if len(s) <= 10:
                    cand = datetime.fromisoformat(s[:10] + "T23:59:00").replace(tzinfo=_TZ_SP)
                else:
                    cand = datetime.fromisoformat(s.replace("Z", "+00:00"))
                    if cand.tzinfo is None:
                        cand = cand.replace(tzinfo=_TZ_SP)
            except Exception:
                continue
            if last_dt is None or cand > last_dt:
                last_dt = cand
    freshness = _freshness_from_ts(last_dt, lang_pt=lang_pt)

    # Actual data calendar span (so UI can explain short windows).
    data_start = data_end = None
    all_ts: list[datetime] = []
    for tr in traces:
        for p in tr.get("points") or []:
            try:
                all_ts.append(datetime.fromisoformat(str(p.get("t")).replace("Z", "+00:00")))
            except Exception:
                pass
    if all_ts:
        data_start = min(all_ts).astimezone(_TZ_SP).date().isoformat()
        data_end = max(all_ts).astimezone(_TZ_SP).date().isoformat()

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
        "chart_title": VAR_CHART_TITLES_PT.get(var, VAR_LABELS_PT.get(var, var)),
        "unit": VAR_UNITS.get(var, ""),
        "active_variables": active_vars,
        "mode": "gallery" if want_multi or len(traces) > 1 else "single",
        "traces": traces,
        "days": days_i,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "data_start": data_start,
        "data_end": data_end,
        "windows": [
            {"id": 7, "label": "7d"},
            {"id": 30, "label": "30d"},
            {"id": 90, "label": "90d"},
        ],
        "points": points,
        "latest": latest,
        "freshness": freshness,
        "last_ts": last_dt.isoformat() if last_dt else None,
        "n_points": sum(t.get("n_points") or 0 for t in traces) if traces else len(points),
        # Always raw measurements (no hourly/daily mean aggregation).
        "resolution": "raw",
    }
