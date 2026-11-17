"""Map Orion sensorType / uom → clima_series + families for the EFC map.

Live efc_clima.clima_series columns:
  date, location_id, source, ingested_at, variable, value

Families (for map + inspection, not for rain cards):
  meteo   — pluviômetro / clima (chuva, temp, UR, barômetro)
  geotech — piezo, nível, inclinômetro, trinca, strain, deslocamento
  health  — bateria, alimentação, sinal (estado do logger, não fenômeno)

A same logger may be mixed (chuva + geotécnico). Map shows one circle; click
lists ports/series. Rain KPIs must only use ``family=meteo`` + ``precip_mm``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

SOURCE_ORION = "orion"
SOURCE_ALIAS_EWS = "ews"

FAMILY_METEO = "meteo"
FAMILY_GEOTECH = "geotech"
FAMILY_HEALTH = "health"

ASSET_METEO = "meteo"
ASSET_GEOTECH = "geotech"
ASSET_MIXED = "mixed"
ASSET_LOGGER = "logger"

# First match wins. Order: specific geotech before generic "pressure"/"mm".
_SENSOR_TYPE_MAP: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"inclin|tilt|tcr\b|mems", re.I), "inclin_deg", FAMILY_GEOTECH),
    (re.compile(r"crack|fissura|junta|joint\s*meter", re.I), "crack_mm", FAMILY_GEOTECH),
    (re.compile(r"strain|extensom|alongament", re.I), "strain_ue", FAMILY_GEOTECH),
    (re.compile(r"displac|desloc|settlement|recalque|deflect", re.I), "desloc_mm", FAMILY_GEOTECH),
    (re.compile(r"piezo|pore\s*press|press[aã]o\s*d.?agua|vw\b|vibrating\s*wire", re.I), "piezo_kpa", FAMILY_GEOTECH),
    (re.compile(r"load\s*cell|c[eé]lula|anchor\s*load", re.I), "carga_kn", FAMILY_GEOTECH),
    (re.compile(r"water\s*level|n[ií]vel|nivel|stage|depth|cota", re.I), "nivel_m", FAMILY_GEOTECH),
    (re.compile(r"rain|precip|pluvi", re.I), "precip_mm", FAMILY_METEO),
    (re.compile(r"flow|discharge|vaz[aã]o", re.I), "vazao_m3s", FAMILY_METEO),
    (re.compile(r"humid|rh\b", re.I), "rh_pct", FAMILY_METEO),
    (re.compile(r"temp", re.I), "temp_c", FAMILY_METEO),
    (re.compile(r"baro", re.I), "pressure_hpa", FAMILY_METEO),
    (re.compile(r"battery\s*volt", re.I), "battery_v", FAMILY_HEALTH),
    (re.compile(r"supply\s*volt|external\s*volt", re.I), "supply_v", FAMILY_HEALTH),
    (re.compile(r"signal|rssi|csq", re.I), "signal_dbm", FAMILY_HEALTH),
    (re.compile(r"switch|digital|relay|state", re.I), "switch_state", FAMILY_HEALTH),
]

_GEOTECH_NAME = re.compile(
    r"piezo|inclin|talude|slope|geotec|trinca|strain|recalque|desloc",
    re.I,
)

VARIABLE_LABEL_PT = {
    "precip_mm": "Chuva",
    "nivel_m": "Nível d'água",
    "piezo_kpa": "Piezômetro",
    "inclin_deg": "Inclinômetro",
    "crack_mm": "Abertura de trinca",
    "strain_ue": "Extensômetro",
    "desloc_mm": "Deslocamento",
    "carga_kn": "Carga",
    "vazao_m3s": "Vazão",
    "temp_c": "Temperatura",
    "rh_pct": "Umidade",
    "pressure_hpa": "Pressão atm.",
    "battery_v": "Bateria",
    "supply_v": "Alimentação",
    "signal_dbm": "Sinal",
    "switch_state": "Estado",
}


def map_sensor_type(sensor_type: Optional[str], uom: Optional[str] = None) -> Optional[str]:
    """Return clima_series variable name, or None if unknown."""
    classified = classify_sensor(sensor_type, uom)
    return classified["variable"] if classified else None


def classify_sensor(sensor_type: Optional[str], uom: Optional[str] = None) -> Optional[dict[str, str]]:
    """Return ``{variable, family, label_pt}`` or None."""
    text = " ".join(x for x in (sensor_type or "", uom or "") if x)
    if not text.strip():
        return None
    for pat, var, fam in _SENSOR_TYPE_MAP:
        if pat.search(text):
            return {"variable": var, "family": fam, "label_pt": VARIABLE_LABEL_PT.get(var, var)}
    u = (uom or "").strip().lower()
    st = sensor_type or ""
    if u in {"mm", "mm/h", "mm/hr"} and re.search(r"rain|precip|pluvi", st, re.I):
        return {"variable": "precip_mm", "family": FAMILY_METEO, "label_pt": VARIABLE_LABEL_PT["precip_mm"]}
    if u in {"kpa", "kpa", "mca"} and re.search(r"press|piezo", st, re.I):
        return {"variable": "piezo_kpa", "family": FAMILY_GEOTECH, "label_pt": VARIABLE_LABEL_PT["piezo_kpa"]}
    if u in {"m", "cm"} and re.search(r"level|nivel|n[ií]vel", st, re.I):
        return {"variable": "nivel_m", "family": FAMILY_GEOTECH, "label_pt": VARIABLE_LABEL_PT["nivel_m"]}
    if u in {"°", "deg", "degree"} and re.search(r"tilt|inclin", st, re.I):
        return {"variable": "inclin_deg", "family": FAMILY_GEOTECH, "label_pt": VARIABLE_LABEL_PT["inclin_deg"]}
    if u in {"m3/s", "m³/s", "cms"}:
        return {"variable": "vazao_m3s", "family": FAMILY_METEO, "label_pt": VARIABLE_LABEL_PT["vazao_m3s"]}
    if u in {"c", "°c", "degc"}:
        return {"variable": "temp_c", "family": FAMILY_METEO, "label_pt": VARIABLE_LABEL_PT["temp_c"]}
    return None


def sensor_family(sensor_type: Optional[str], uom: Optional[str] = None) -> Optional[str]:
    hit = classify_sensor(sensor_type, uom)
    return hit["family"] if hit else None


def device_asset_class(device: Mapping[str, Any]) -> str:
    """meteo | geotech | mixed | logger — for map color, not rain KPI."""
    families: set[str] = set()
    name = str(device.get("deviceName") or device.get("name") or "")
    if _GEOTECH_NAME.search(name):
        families.add(FAMILY_GEOTECH)
    for s in list(device.get("sensors") or []) + list(device.get("softSensors") or []):
        fam = sensor_family(s.get("sensorType"), s.get("uom"))
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


def km_hint_from_name(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    m = re.search(r"\bkm\s*(\d{1,4})\b", name, re.I)
    if m:
        return int(m.group(1))
    return None


def station_code_from_device(device: Mapping[str, Any]) -> str:
    """Human station code: EWS6536, BS08687S, or IMEI_/ORION_ fallback."""
    name = str(
        device.get("deviceName")
        or device.get("device_name")
        or device.get("name")
        or ""
    ).strip()
    m = re.search(r"\b(EWS\d+|BS\d+\w*|BC\d+\w*)\b", name, re.I)
    if m:
        return m.group(1).upper()
    serial = device.get("serialNumber") or device.get("serial_number")
    if serial:
        return f"IMEI_{serial}"
    did = device.get("deviceId") or device.get("device_id")
    if did is not None:
        return f"ORION_{did}"
    return "ORION_UNKNOWN"


station_id_from_device = station_code_from_device


def location_id_from_device(device: Mapping[str, Any]) -> str:
    return f"ews_{station_code_from_device(device)}"


def reading_to_clima_row(
    *,
    device: Mapping[str, Any],
    sensor_meta: Mapping[str, Any],
    reading: Mapping[str, Any],
    prefer_soft: bool = True,
    source: str = SOURCE_ORION,
    ingested_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    del prefer_soft
    kind = sensor_meta.get("kind")
    classified = classify_sensor(
        sensor_meta.get("sensor_type") or sensor_meta.get("sensorType"),
        sensor_meta.get("uom"),
    )
    if classified is None:
        return None
    variable = classified["variable"]
    family = classified["family"]
    # Rain KPI path: health never; geotech never as precip.
    if family == FAMILY_HEALTH:
        # Still store battery/signal for logger health on the map popup.
        pass

    value = reading.get("sensorValue")
    if value is None:
        return None

    device_like = device if ("deviceName" in device or "deviceId" in device) else sensor_meta
    code = station_code_from_device(device_like)
    loc_id = location_id_from_device(device_like)
    now = ingested_at or datetime.now(timezone.utc).isoformat()
    return {
        "date": reading.get("readingDate"),
        "location_id": loc_id,
        "source": source,
        "ingested_at": now,
        "variable": variable,
        "value": float(value),
        "station_id": code,
        "obs_time": reading.get("readingDate"),
        "lat": device.get("latitude") if "latitude" in device else sensor_meta.get("latitude"),
        "lon": device.get("longitude") if "longitude" in device else sensor_meta.get("longitude"),
        "meta_sensor_id": reading.get("sensorId") or sensor_meta.get("sensor_id"),
        "meta_soft_sensor_id": reading.get("softSensorId") or sensor_meta.get("soft_sensor_id"),
        "meta_sensor_type": sensor_meta.get("sensor_type") or sensor_meta.get("sensorType"),
        "meta_uom": sensor_meta.get("uom"),
        "meta_kind": kind,
        "meta_family": family,
        "meta_asset_class": device_asset_class(device_like),
        "meta_device_id": device.get("deviceId") or sensor_meta.get("device_id"),
        "meta_serial": device.get("serialNumber") or sensor_meta.get("serial_number"),
        "meta_battery_pct": device.get("batteryPercentage") or sensor_meta.get("battery_pct"),
        "meta_status": device.get("status") or sensor_meta.get("status"),
        "meta_channel": sensor_meta.get("channel_number") or sensor_meta.get("channelNumber"),
    }


def clima_series_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("date", "location_id", "source", "ingested_at", "variable", "value")
    out = []
    for r in rows:
        out.append({k: r[k] for k in keep if k in r})
    return out


def device_to_location_row(device: Mapping[str, Any], source: str = SOURCE_ORION) -> dict[str, Any]:
    code = station_code_from_device(device)
    extra = {
        "orion_device_id": device.get("deviceId"),
        "serial_number": device.get("serialNumber"),
        "reference": device.get("reference"),
        "status": device.get("status"),
        "battery_pct": device.get("batteryPercentage"),
        "last_upload": device.get("lastUpload"),
        "date_installed": device.get("dateInstalled"),
        "asset_class": device_asset_class(device),
        "km_hint": km_hint_from_name(device.get("deviceName")),
    }
    return {
        "source": source,
        "location_id": location_id_from_device(device),
        "station_id": code,
        "name": device.get("deviceName"),
        "lat": device.get("latitude"),
        "lon": device.get("longitude"),
        "extra": extra,
    }
