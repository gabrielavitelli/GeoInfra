"""Map Orion sensorType / uom → climaticsystem clima_series variables.

Existing efc_clima.clima_series conventions (from prior EFC work):
  source ∈ {ana, estacoes, …}   ← we add ``orion`` (alias ops: ``ews``)
  station_id like estacao_<code> or free-form code
  variable ∈ {precip_mm, nivel_m, vazao_m3s, temp_c, wind_*, …}
  value FLOAT, obs_time / date TIMESTAMP|DATE

Orion sensorType strings are free-text per device config. Prefer soft sensors
(calibrated) when both raw + soft exist for the same physical channel.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

SOURCE_ORION = "orion"
SOURCE_ALIAS_EWS = "ews"  # optional dual-write / monitor filter alias

# sensorType (case-insensitive substring) → clima_series.variable
_SENSOR_TYPE_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"water\s*level|nível|nivel|stage|depth|cota", re.I), "nivel_m"),
    (re.compile(r"rain|precip|pluvi", re.I), "precip_mm"),
    (re.compile(r"flow|discharge|vaz[aã]o", re.I), "vazao_m3s"),
    (re.compile(r"temp", re.I), "temp_c"),
    (re.compile(r"humid|rh\b", re.I), "rh_pct"),
    (re.compile(r"baro|pressure|press", re.I), "pressure_hpa"),
    (re.compile(r"battery\s*volt", re.I), "battery_v"),
    (re.compile(r"supply\s*volt|external\s*volt", re.I), "supply_v"),
    (re.compile(r"signal|rssi|csq", re.I), "signal_dbm"),
    (re.compile(r"switch|digital|relay|state", re.I), "switch_state"),
]


def map_sensor_type(sensor_type: Optional[str], uom: Optional[str] = None) -> Optional[str]:
    """Return clima_series variable name, or None if unknown (keep raw in staging)."""
    text = " ".join(x for x in (sensor_type or "", uom or "") if x)
    if not text.strip():
        return None
    for pat, var in _SENSOR_TYPE_MAP:
        if pat.search(text):
            return var
    # uom-only heuristics
    u = (uom or "").strip().lower()
    if u in {"mm", "mm/h", "mm/hr"}:
        return "precip_mm"
    if u in {"m", "cm", "mm"} and sensor_type and re.search(r"level|nivel|nível", sensor_type, re.I):
        return "nivel_m"
    if u in {"m3/s", "m³/s", "cms"}:
        return "vazao_m3s"
    if u in {"c", "°c", "degc"}:
        return "temp_c"
    return None


def station_id_from_device(device: Mapping[str, Any]) -> str:
    """Stable station_id for BQ / monitor.

    Prefer deviceName code like ``EWS6536`` when present; else IMEI serialNumber.
    """
    name = str(
        device.get("deviceName")
        or device.get("device_name")
        or device.get("name")
        or ""
    ).strip()
    m = re.search(r"\b(EWS\d+|BS\d+\w*)\b", name, re.I)
    if m:
        return m.group(1).upper()
    serial = device.get("serialNumber") or device.get("serial_number")
    if serial:
        return f"IMEI_{serial}"
    did = device.get("deviceId") or device.get("device_id")
    if did is not None:
        return f"ORION_{did}"
    return "ORION_UNKNOWN"


def reading_to_clima_row(
    *,
    device: Mapping[str, Any],
    sensor_meta: Mapping[str, Any],
    reading: Mapping[str, Any],
    prefer_soft: bool = True,
    source: str = SOURCE_ORION,
) -> Optional[dict[str, Any]]:
    """Build one clima_series-shaped dict, or None if sensor not mapped / skipped."""
    kind = sensor_meta.get("kind")
    if prefer_soft and kind == "raw":
        # Caller should pass soft rows when available; raw still allowed if only raw exists.
        pass

    variable = map_sensor_type(
        sensor_meta.get("sensor_type") or sensor_meta.get("sensorType"),
        sensor_meta.get("uom"),
    )
    if variable is None:
        # Keep unmapped channels out of clima_series; they stay in orion_readings staging.
        return None

    value = reading.get("sensorValue")
    if value is None:
        return None

    sid = station_id_from_device(device if "deviceName" in device or "deviceId" in device else sensor_meta)
    return {
        "source": source,
        "station_id": sid,
        "variable": variable,
        "value": float(value),
        "obs_time": reading.get("readingDate"),
        "lat": device.get("latitude") if "latitude" in device else sensor_meta.get("latitude"),
        "lon": device.get("longitude") if "longitude" in device else sensor_meta.get("longitude"),
        "meta_sensor_id": reading.get("sensorId") or sensor_meta.get("sensor_id"),
        "meta_soft_sensor_id": reading.get("softSensorId") or sensor_meta.get("soft_sensor_id"),
        "meta_sensor_type": sensor_meta.get("sensor_type") or sensor_meta.get("sensorType"),
        "meta_uom": sensor_meta.get("uom"),
        "meta_kind": kind,
        "meta_device_id": device.get("deviceId") or sensor_meta.get("device_id"),
        "meta_serial": device.get("serialNumber") or sensor_meta.get("serial_number"),
        "meta_battery_pct": device.get("batteryPercentage") or sensor_meta.get("battery_pct"),
        "meta_status": device.get("status") or sensor_meta.get("status"),
    }


def device_to_location_row(device: Mapping[str, Any], source: str = SOURCE_ORION) -> dict[str, Any]:
    """Row for efc_clima.locations (monitor map catalog)."""
    return {
        "source": source,
        "station_id": station_id_from_device(device),
        "name": device.get("deviceName"),
        "lat": device.get("latitude"),
        "lon": device.get("longitude"),
        "extra": {
            "orion_device_id": device.get("deviceId"),
            "serial_number": device.get("serialNumber"),
            "reference": device.get("reference"),
            "status": device.get("status"),
            "battery_pct": device.get("batteryPercentage"),
            "last_upload": device.get("lastUpload"),
            "date_installed": device.get("dateInstalled"),
        },
    }
