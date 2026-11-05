"""Map Orion sensorType / uom → climaticsystem clima_series rows.

Live efc_clima.clima_series columns (from monitor load logs):
  date, location_id, source, ingested_at, variable, value

ANA/BNDMET use location_id like ``estacao_A044`` / ``estacao_23310000``.
Orion/EWS uses ``ews_<CODE>`` (e.g. ``ews_EWS6536``).

source: ``orion`` (primary). Alias ``ews`` available via flag for monitor filters.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

SOURCE_ORION = "orion"
SOURCE_ALIAS_EWS = "ews"

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
    u = (uom or "").strip().lower()
    if u in {"mm", "mm/h", "mm/hr"}:
        return "precip_mm"
    if u in {"m", "cm"} and sensor_type and re.search(r"level|nivel|nível", sensor_type, re.I):
        return "nivel_m"
    if u in {"m3/s", "m³/s", "cms"}:
        return "vazao_m3s"
    if u in {"c", "°c", "degc"}:
        return "temp_c"
    return None


def station_code_from_device(device: Mapping[str, Any]) -> str:
    """Human station code: EWS6536, BS08687S, or IMEI_/ORION_ fallback."""
    name = str(
        device.get("deviceName")
        or device.get("device_name")
        or device.get("name")
        or ""
    ).strip()
    # EWS6536, EWSBC07495S, BS08687S, BC07507S, …
    m = re.search(r"\b((?:EWS|BS|BC)[A-Z]*\d+\w*)\b", name, re.I)
    if m:
        return m.group(1).upper()
    # Leading token before " - " when it looks like a code
    head = re.split(r"\s*[-–—]\s*", name, maxsplit=1)[0].strip()
    if head and re.fullmatch(r"[A-Z]{2,}\d+\w*", head, re.I):
        return head.upper()
    serial = device.get("serialNumber") or device.get("serial_number")
    if serial:
        return f"IMEI_{serial}"
    did = device.get("deviceId") or device.get("device_id")
    if did is not None:
        return f"ORION_{did}"
    return "ORION_UNKNOWN"


# Back-compat alias used by ingest.py
station_id_from_device = station_code_from_device


def location_id_from_device(device: Mapping[str, Any]) -> str:
    """clima_series.location_id / locations key — prefix ews_."""
    return f"ews_{station_code_from_device(device)}"


def clean_device_display_name(raw: Optional[str], *, station_code: Optional[str] = None) -> str:
    """``EWS6536  - Abrigo km 149`` → ``Abrigo km 149`` (fallback: código)."""
    text = " ".join(str(raw or "").split()).strip()
    if not text:
        return (station_code or "").strip() or "EWS"
    # Drop leading station code + separators
    text2 = re.sub(r"^(EWS\d+|BS\d+\w*)\s*[-–—:/]*\s*", "", text, flags=re.I).strip()
    if text2:
        return text2
    return text


def km_efc_from_device_name(raw: Optional[str]) -> Optional[float]:
    """Parse ``Abrigo km 149`` / ``km 734`` → 149.0."""
    m = re.search(r"\bkm\s*(\d+(?:[.,]\d+)?)\b", str(raw or ""), re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def is_precip_sensor(sensor_type: Optional[str], uom: Optional[str] = None) -> bool:
    """True when sensor maps to precip_mm (Rainfall / soft precip)."""
    return map_sensor_type(sensor_type, uom) == "precip_mm"

def reading_to_clima_row(
    *,
    device: Mapping[str, Any],
    sensor_meta: Mapping[str, Any],
    reading: Mapping[str, Any],
    prefer_soft: bool = True,
    source: str = SOURCE_ORION,
    ingested_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Build one clima_series row matching live schema + optional meta_* for staging CSV."""
    del prefer_soft  # selection happens upstream
    kind = sensor_meta.get("kind")
    variable = map_sensor_type(
        sensor_meta.get("sensor_type") or sensor_meta.get("sensorType"),
        sensor_meta.get("uom"),
    )
    if variable is None:
        return None

    value = reading.get("sensorValue")
    if value is None:
        return None

    device_like = device if ("deviceName" in device or "deviceId" in device) else sensor_meta
    code = station_code_from_device(device_like)
    loc_id = location_id_from_device(device_like)
    now = ingested_at or datetime.now(timezone.utc).isoformat()
    return {
        # Live clima_series columns:
        "date": reading.get("readingDate"),
        "location_id": loc_id,
        "source": source,
        "ingested_at": now,
        "variable": variable,
        "value": float(value),
        # Helpers / GCS consolidated extras (stripped before BQ load):
        "station_id": code,
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


def clima_series_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only columns accepted by efc_clima.clima_series."""
    keep = ("date", "location_id", "source", "ingested_at", "variable", "value")
    out = []
    for r in rows:
        out.append({k: r[k] for k in keep if k in r})
    return out


def device_to_location_row(device: Mapping[str, Any], source: str = SOURCE_ORION) -> dict[str, Any]:
    """Row for efc_clima.locations / orion_devices catalog."""
    code = station_code_from_device(device)
    raw_name = device.get("deviceName")
    display = clean_device_display_name(raw_name, station_code=code)
    return {
        "source": source,
        "location_id": location_id_from_device(device),
        "station_id": code,
        "name": display,
        "name_raw": raw_name,
        "km_efc": km_efc_from_device_name(raw_name),
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
            "display_name": display,
        },
    }
