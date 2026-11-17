"""Map ZENTRA measurements → clima_series + families for the EFC map.

Live efc_clima.clima_series columns:
  date, location_id, source, ingested_at, variable, value
  (+ optional forecast_* ignored here)

Families:
  meteo   — ATMOS precip / air climate
  geotech — TEROS soil moisture (VWC) / water potential
  health  — battery, signal (logger state)

location_id = zentra_<device_id with hyphens → underscores> e.g. zentra_z6_35936
source = zentra
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

SOURCE_ZENTRA = "zentra"

FAMILY_METEO = "meteo"
FAMILY_GEOTECH = "geotech"
FAMILY_HEALTH = "health"

ASSET_METEO = "meteo"
ASSET_GEOTECH = "geotech"
ASSET_MIXED = "mixed"
ASSET_LOGGER = "logger"

# Oct 27–28 cluster + org extras (priority for ingest ordering).
PRIORITY_DEVICE_IDS = (
    "z6-35936",
    "z6-35938",
    "z6-35939",
    "z6-35940",
    "z6-35946",
    "z6-35948",
    "z6-35947",
    "z6-35951",
    "z6-35952",
    "z6-35937",
    "z6-35950",
)

# (sensor_name_pat, measurement_pat, variable, family, unit_scale)
# First match wins. unit_scale multiplies the raw value (e.g. mV→V, kPa→hPa).
_MEASUREMENT_MAP: list[tuple[re.Pattern[str], re.Pattern[str], str, str, float]] = [
    # ATMOS precip (meteo)
    (
        re.compile(r"ATMOS|Rain\s*Gauge|Precipitation", re.I),
        re.compile(r"^Precipitation$", re.I),
        "precip_mm",
        FAMILY_METEO,
        1.0,
    ),
    # TEROS 12 soil moisture / VWC (geotech-ish)
    (
        re.compile(r"TEROS\s*12|TEROS\s*10|TEROS\s*11|TEROS\s*54", re.I),
        re.compile(r"^Water\s*Content$|^VWC$", re.I),
        "soil_vwc",
        FAMILY_GEOTECH,
        1.0,
    ),
    # TEROS 21 water potential (geotech)
    (
        re.compile(r"TEROS\s*21|TEROS\s*22|MPS", re.I),
        re.compile(r"^Matric\s*Potential$|^Water\s*Potential$", re.I),
        "water_potential_kpa",
        FAMILY_GEOTECH,
        1.0,
    ),
    # Useful soil temp alongside TEROS
    (
        re.compile(r"TEROS", re.I),
        re.compile(r"^Soil\s*Temperature$", re.I),
        "soil_temp_c",
        FAMILY_GEOTECH,
        1.0,
    ),
    # ATMOS air climate (keep lean)
    (
        re.compile(r"ATMOS", re.I),
        re.compile(r"^Air\s*Temperature$", re.I),
        "temp_c",
        FAMILY_METEO,
        1.0,
    ),
    (
        re.compile(r"ATMOS", re.I),
        re.compile(r"^Relative\s*Humidity$", re.I),
        "rh_pct",
        FAMILY_METEO,
        1.0,
    ),
    (
        re.compile(r"ATMOS|Barometer", re.I),
        re.compile(r"^Atmospheric\s*Pressure$", re.I),
        "pressure_hpa",
        FAMILY_METEO,
        10.0,  # kPa → hPa
    ),
    # Health
    (
        re.compile(r"Battery", re.I),
        re.compile(r"^Battery\s*Percent$", re.I),
        "battery_pct",
        FAMILY_HEALTH,
        1.0,
    ),
    (
        re.compile(r"Battery", re.I),
        re.compile(r"^Battery\s*Voltage$", re.I),
        "battery_v",
        FAMILY_HEALTH,
        0.001,  # mV → V
    ),
    (
        re.compile(r"Signal", re.I),
        re.compile(r"^Signal$", re.I),
        "signal_pct",
        FAMILY_HEALTH,
        1.0,
    ),
]

VARIABLE_LABEL_PT = {
    "precip_mm": "Chuva",
    "soil_vwc": "Umidade do solo (VWC)",
    "water_potential_kpa": "Potencial hídrico",
    "soil_temp_c": "Temp. do solo",
    "temp_c": "Temperatura do ar",
    "rh_pct": "Umidade relativa",
    "pressure_hpa": "Pressão atm.",
    "battery_pct": "Bateria %",
    "battery_v": "Bateria V",
    "signal_pct": "Sinal",
}

_GEOTECH_SENSOR = re.compile(r"TEROS|MPS|soil", re.I)
_METEO_SENSOR = re.compile(r"ATMOS|Rain|Precipitation|Barometer", re.I)


def classify_measurement(
    sensor_name: Optional[str],
    measurement: Optional[str],
    unit: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return ``{variable, family, label_pt, scale}`` or None."""
    del unit  # reserved for future unit-aware remaps
    sn = sensor_name or ""
    ms = measurement or ""
    if not ms.strip():
        return None
    for sn_pat, ms_pat, var, fam, scale in _MEASUREMENT_MAP:
        if sn_pat.search(sn) and ms_pat.search(ms):
            return {
                "variable": var,
                "family": fam,
                "label_pt": VARIABLE_LABEL_PT.get(var, var),
                "scale": scale,
            }
    # Fallback: bare Precipitation without ATMOS in sensor_name
    if re.search(r"^Precipitation$", ms, re.I):
        return {
            "variable": "precip_mm",
            "family": FAMILY_METEO,
            "label_pt": VARIABLE_LABEL_PT["precip_mm"],
            "scale": 1.0,
        }
    return None


def map_measurement(
    sensor_name: Optional[str],
    measurement: Optional[str],
    unit: Optional[str] = None,
) -> Optional[str]:
    hit = classify_measurement(sensor_name, measurement, unit)
    return hit["variable"] if hit else None


def device_asset_class(device: Mapping[str, Any]) -> str:
    """meteo | geotech | mixed | logger."""
    families: set[str] = set()
    for p in device.get("ports") or []:
        name = str(p.get("sensor_short_name") or "")
        if _GEOTECH_SENSOR.search(name):
            families.add(FAMILY_GEOTECH)
        if _METEO_SENSOR.search(name):
            families.add(FAMILY_METEO)
    # Also peek recent measurement names if present on flattened rows
    for s in device.get("sensors_flat") or []:
        fam = (classify_measurement(s.get("sensor_name"), s.get("measurement")) or {}).get(
            "family"
        )
        if fam in {FAMILY_METEO, FAMILY_GEOTECH}:
            families.add(fam)
    has_m = FAMILY_METEO in families
    has_g = FAMILY_GEOTECH in families
    if has_m and has_g:
        return ASSET_MIXED
    if has_g:
        return ASSET_GEOTECH
    if has_m:
        return ASSET_METEO
    return ASSET_LOGGER


def station_id_from_device(device: Mapping[str, Any]) -> str:
    did = str(device.get("device_id") or device.get("deviceId") or "").strip()
    if did:
        return did
    name = str(device.get("name") or device.get("device_name") or "").strip()
    return name or "ZENTRA_UNKNOWN"


def location_id_from_device(device: Mapping[str, Any]) -> str:
    """zentra_z6_35936 — stable id for clima_series / map join."""
    sid = station_id_from_device(device).lower().replace("-", "_")
    if sid.startswith("zentra_"):
        return sid
    return f"zentra_{sid}"


def _device_lat_lon(device: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    loc = device.get("location")
    if isinstance(loc, dict):
        return loc.get("latitude"), loc.get("longitude")
    return device.get("latitude"), device.get("longitude")


def _iso_from_value(value_row: Mapping[str, Any]) -> Optional[str]:
    dt = value_row.get("datetime")
    if isinstance(dt, str) and dt.strip():
        return dt
    ts = value_row.get("timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return None
    return None


def _date_str_from_value(value_row: Mapping[str, Any]) -> Optional[str]:
    """BQ clima_series.date is DATE — emit YYYY-MM-DD (UTC from timestamp when possible)."""
    ts = value_row.get("timestamp")
    if ts is not None:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            pass
    dt = value_row.get("datetime")
    if isinstance(dt, str) and len(dt) >= 10:
        # Prefer explicit UTC if Z suffix; else take leading date (local wall).
        return dt[:10]
    return None


def reading_to_clima_row(
    *,
    device: Mapping[str, Any],
    value_row: Mapping[str, Any],
    source: str = SOURCE_ZENTRA,
    ingested_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if int(value_row.get("error_code") or 0) != 0:
        return None
    raw_val = value_row.get("value")
    if raw_val is None:
        return None
    classified = classify_measurement(
        value_row.get("sensor_name"),
        value_row.get("measurement"),
        value_row.get("unit"),
    )
    if classified is None:
        return None
    date_s = _date_str_from_value(value_row)
    if not date_s:
        return None
    lat, lon = _device_lat_lon(device)
    now = ingested_at or datetime.now(timezone.utc).isoformat()
    scale = float(classified["scale"])
    return {
        "date": date_s,
        "location_id": location_id_from_device(device),
        "source": source,
        "ingested_at": now,
        "variable": classified["variable"],
        "value": float(raw_val) * scale,
        "station_id": station_id_from_device(device),
        "obs_time": _iso_from_value(value_row),
        "lat": lat,
        "lon": lon,
        "meta_family": classified["family"],
        "meta_asset_class": device_asset_class(device),
        "meta_sensor_name": value_row.get("sensor_name"),
        "meta_measurement": value_row.get("measurement"),
        "meta_unit": value_row.get("unit"),
        "meta_port": value_row.get("port_num"),
        "meta_position": value_row.get("position"),
        "meta_reading_id": value_row.get("reading_id"),
        "meta_device_id": device.get("device_id"),
    }


def clima_series_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("date", "location_id", "source", "ingested_at", "variable", "value")
    out = []
    for r in rows:
        out.append({k: r[k] for k in keep if k in r})
    return out


def device_to_location_row(device: Mapping[str, Any], source: str = SOURCE_ZENTRA) -> dict[str, Any]:
    lat, lon = _device_lat_lon(device)
    ports = device.get("ports") or []
    settings = device.get("settings") or {}
    data_range = device.get("data_range") or {}
    connectivity = device.get("connectivity") or {}
    from scripts.zentra.client import device_status  # local import to avoid cycle at module load

    extra = {
        "zentra_device_id": device.get("device_id"),
        "model": device.get("model"),
        "device_type": device.get("device_type"),
        "organization_id": device.get("organization_id"),
        "organization_name": device.get("organization_name"),
        "status": device_status(device),
        "n_ports": len(ports),
        "sensors": sorted(
            {str(p.get("sensor_short_name") or "") for p in ports if p.get("sensor_short_name")}
        ),
        "logging_interval_min": settings.get("logging_interval"),
        "upload_frequency_min": settings.get("upload_frequency"),
        "first_measurement": data_range.get("first_measurement"),
        "last_measurement": data_range.get("last_measurement"),
        "connectivity_type": connectivity.get("connectivity_type"),
        "asset_class": device_asset_class(device),
        "can_read_data": device.get("can_read_data"),
    }
    return {
        "source": source,
        "location_id": location_id_from_device(device),
        "station_id": station_id_from_device(device),
        "name": device.get("name") or device.get("device_id"),
        "lat": lat,
        "lon": lon,
        "extra": extra,
    }


def max_reading_id(values: list[Mapping[str, Any]]) -> Optional[int]:
    ids = [int(v["reading_id"]) for v in values if v.get("reading_id") is not None]
    return max(ids) if ids else None
