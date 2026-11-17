"""GeoJSON + popup for visão geral: one circle per logger.

Click payload is for a geotech + a manager sending inspection crews:
- where (km, coords)
- logger health (online / stale / dead, battery)
- ports (channel / raw vs soft)
- which time series exist (meteo vs geotech — never mix into rain KPI)
- copy-ready inspection line
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Mapping, Optional

from scripts.orion_ews.client import flatten_device_sensors
from scripts.orion_ews.mapping import (
    ASSET_GEOTECH,
    ASSET_LOGGER,
    ASSET_METEO,
    ASSET_MIXED,
    FAMILY_GEOTECH,
    FAMILY_HEALTH,
    FAMILY_METEO,
    classify_sensor,
    device_asset_class,
    km_hint_from_name,
    location_id_from_device,
    station_code_from_device,
)
from scripts.orion_ews.siting import siting_report

# Distinct from BNDMET green / ANA cyan / old EWS yellow.
COLOR_METEO = "#38bdf8"  # céu — estação
COLOR_GEOTECH = "#ea580c"  # terra — sensor geotécnico
COLOR_MIXED = "#a855f7"  # ambos no mesmo logger
COLOR_LOGGER = "#94a3b8"

STALE_HOURS = 24.0
DEAD_HOURS = 72.0


def _parse_ts(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    text = str(val).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def logger_freshness(last_upload: Any, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """online | stale | dead | unknown — drives inspection priority."""
    now = now or datetime.now(timezone.utc)
    ts = _parse_ts(last_upload)
    if ts is None:
        return {"state": "unknown", "hours": None, "label_pt": "sem último envio", "priority": 2}
    hours = (now - ts).total_seconds() / 3600.0
    if hours <= STALE_HOURS:
        return {"state": "online", "hours": round(hours, 1), "label_pt": "online", "priority": 0}
    if hours <= DEAD_HOURS:
        return {"state": "stale", "hours": round(hours, 1), "label_pt": f"atrasado {hours:.0f} h", "priority": 1}
    return {"state": "dead", "hours": round(hours, 1), "label_pt": f"parado {hours:.0f} h", "priority": 2}


def _port_row(sensor: Mapping[str, Any]) -> dict[str, Any]:
    classified = classify_sensor(sensor.get("sensor_type"), sensor.get("uom")) or {}
    ch = sensor.get("channel_number")
    kind = sensor.get("kind") or "raw"
    if kind == "soft":
        port = "soft"
    elif ch is not None and str(ch).strip() != "":
        port = f"ch {ch}"
    else:
        port = "ch —"
    family = classified.get("family")
    return {
        "port": port,
        "kind": kind,
        "sensor_id": sensor.get("sensor_id"),
        "soft_sensor_id": sensor.get("soft_sensor_id"),
        "sensor_type": sensor.get("sensor_type"),
        "custom_name": sensor.get("custom_name"),
        "uom": sensor.get("uom"),
        "scada_tag": sensor.get("scada_tag"),
        "variable": classified.get("variable"),
        "family": family,
        "label_pt": classified.get("label_pt") or sensor.get("sensor_type") or "não mapeado",
        "has_series": bool(classified.get("variable")),
    }


def _series_from_ports(ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for p in ports:
        var = p.get("variable")
        if not var:
            continue
        if var not in seen:
            seen[var] = {
                "variable": var,
                "family": p.get("family"),
                "label_pt": p.get("label_pt"),
                "ports": [],
            }
        seen[var]["ports"].append(p["port"])
    order = {FAMILY_GEOTECH: 0, FAMILY_METEO: 1, FAMILY_HEALTH: 2}
    return sorted(seen.values(), key=lambda s: (order.get(s["family"], 9), s["variable"]))


def color_for_class(asset_class: str) -> str:
    return {
        ASSET_METEO: COLOR_METEO,
        ASSET_GEOTECH: COLOR_GEOTECH,
        ASSET_MIXED: COLOR_MIXED,
        ASSET_LOGGER: COLOR_LOGGER,
    }.get(asset_class, COLOR_LOGGER)


def layer_id(asset_class: str) -> str:
    """Primary layer. Mixed loggers also appear on both GeoJSON layers."""
    if asset_class == ASSET_GEOTECH:
        return "geotech"
    if asset_class == ASSET_MIXED:
        return "mixed"
    if asset_class == ASSET_METEO:
        return "meteo"
    return "logger"


def device_to_map_asset(device: Mapping[str, Any], *, now: Optional[datetime] = None) -> dict[str, Any]:
    code = station_code_from_device(device)
    name = str(device.get("deviceName") or code)
    asset_class = device_asset_class(device)
    ports = [_port_row(s) for s in flatten_device_sensors([device])]
    series = _series_from_ports(ports)
    fresh = logger_freshness(device.get("lastUpload"), now=now)
    km = km_hint_from_name(name)
    bat = device.get("batteryPercentage")
    inspect_bits = [
        f"km {km}" if km is not None else None,
        code,
        name,
        fresh["label_pt"],
        f"bat {bat}%" if bat is not None else None,
    ]
    inspect_line = " · ".join(str(x) for x in inspect_bits if x)
    return {
        "location_id": location_id_from_device(device),
        "station_id": code,
        "name": name,
        "lat": device.get("latitude"),
        "lon": device.get("longitude"),
        "km": km,
        "asset_class": asset_class,
        "layer": layer_id(asset_class),
        "color": color_for_class(asset_class),
        "status": device.get("status"),
        "battery_pct": bat,
        "serial_number": device.get("serialNumber"),
        "orion_device_id": device.get("deviceId"),
        "last_upload": device.get("lastUpload"),
        "freshness": fresh,
        "ports": ports,
        "series": series,
        "n_ports": len(ports),
        "n_series": len(series),
        "has_geotech": any(p.get("family") == FAMILY_GEOTECH for p in ports),
        "has_meteo": any(p.get("family") == FAMILY_METEO for p in ports),
        "inspect_line": inspect_line,
        "dashboard_url": "https://estacao-meteorologica-894301304159.us-central1.run.app/",
    }


def assets_to_geojson(assets: list[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for a in assets:
        lat, lon = a.get("lat"), a.get("lon")
        if lat is None or lon is None:
            continue
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon_f, lat_f]},
                "properties": {k: v for k, v in a.items() if k not in ("lat", "lon")},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_map_catalog(devices: list[Mapping[str, Any]], *, now: Optional[datetime] = None) -> dict[str, Any]:
    assets = [device_to_map_asset(d, now=now) for d in devices]
    layers = {
        "meteo": [a for a in assets if a["layer"] == "meteo"],
        "geotech": [a for a in assets if a["layer"] == "geotech"],
        "mixed": [a for a in assets if a["layer"] == "mixed"],
        "logger": [a for a in assets if a["layer"] == "logger"],
    }
    # Mixed = same logger with rain + geotech: show on both map toggles.
    return {
        "pulled_at_utc": (now or datetime.now(timezone.utc)).isoformat(),
        "siting": siting_report(devices),
        "legend": [
            {"id": "meteo", "label_pt": "Estação", "color": COLOR_METEO, "hint": "chuva / clima"},
            {"id": "geotech", "label_pt": "Geotécnico", "color": COLOR_GEOTECH, "hint": "nível, piezo, inclinômetro"},
            {"id": "mixed", "label_pt": "Misto", "color": COLOR_MIXED, "hint": "chuva + geotécnico no mesmo logger"},
        ],
        "counts": {k: len(v) for k, v in layers.items()},
        "assets": assets,
        "geojson": {
            "meteo": assets_to_geojson(layers["meteo"] + layers["mixed"]),
            "geotech": assets_to_geojson(layers["geotech"] + layers["mixed"]),
            "logger": assets_to_geojson(layers["logger"]),
        },
    }


def popup_html(asset: Mapping[str, Any]) -> str:
    """Compact HTML for Leaflet/MapLibre — inspection-first, little chrome."""
    cls = asset.get("asset_class") or ""
    tag = {
        ASSET_METEO: "Estação",
        ASSET_GEOTECH: "Geotécnico",
        ASSET_MIXED: "Estação + geotécnico",
        ASSET_LOGGER: "Logger",
    }.get(cls, cls)
    km = asset.get("km")
    fresh = asset.get("freshness") or {}
    bat = asset.get("battery_pct")
    rows = []
    for p in asset.get("ports") or []:
        fam = p.get("family") or "—"
        series = "série" if p.get("has_series") else "sem série"
        label = p.get("custom_name") or p.get("label_pt")
        rows.append(
            f"<tr><td>{escape(str(p.get('port')))}</td>"
            f"<td>{escape(str(label))}</td>"
            f"<td>{escape(str(p.get('uom') or ''))}</td>"
            f"<td>{escape(str(fam))}</td>"
            f"<td>{escape(series)}</td></tr>"
        )
    series_bits = ", ".join(
        escape(str(s.get("label_pt"))) for s in (asset.get("series") or []) if s.get("family") != FAMILY_HEALTH
    ) or "—"
    inspect = escape(str(asset.get("inspect_line") or ""))
    dash = escape(str(asset.get("dashboard_url") or ""))
    km_txt = f"km {km}" if km is not None else "km —"
    bat_txt = f"{bat}%" if bat is not None else "—"
    return (
        f"<div class='efc-asset-pop' data-class='{escape(str(cls))}'>"
        f"<div class='efc-asset-pop__tag'>{escape(tag)}</div>"
        f"<strong>{escape(str(asset.get('name') or ''))}</strong>"
        f"<div class='efc-asset-pop__meta'>{escape(str(asset.get('station_id')))} · {km_txt} · "
        f"{escape(str(fresh.get('label_pt') or ''))} · bat {escape(str(bat_txt))}</div>"
        f"<div class='efc-asset-pop__series'><b>Séries</b> {series_bits}</div>"
        f"<table class='efc-asset-pop__ports'><thead><tr>"
        f"<th>Porta</th><th>Sensor</th><th>un.</th><th>tipo</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        f"<div class='efc-asset-pop__act'>"
        f"<button type='button' data-copy='{inspect}'>copiar p/ inspeção</button> "
        f"<a href='{dash}' target='_blank' rel='noopener'>dashboard</a>"
        f"</div></div>"
    )
