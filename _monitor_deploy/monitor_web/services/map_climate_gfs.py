"""Camada clima 2D (GFS via Open-Meteo) amostrada nos km EFC."""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL = float(os.environ.get("MONITOR_GFS_LAYER_TTL_SEC", "1800") or "1800")


def _railway_sample(step_km: int = 25) -> list[dict[str, Any]]:
    from lib_efc import get_railway_data, position_at_km

    main_line, cum_km, _mk, _lines = get_railway_data()
    if main_line is None or cum_km is None:
        return []
    km_max = int(float(cum_km[-1]))
    out = []
    for km in range(0, km_max + 1, max(5, int(step_km))):
        pos = position_at_km(float(km), main_line, cum_km)
        if pos is None:
            continue
        out.append({"km": int(km), "lat": float(pos[0]), "lon": float(pos[1])})
    return out


def _fetch_open_meteo(lat: float, lon: float) -> dict[str, float | None]:
    """GFS precip / wind / soil moisture (Open-Meteo)."""
    qs = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": "precipitation,wind_speed_10m,soil_moisture_0_to_7cm",
            "forecast_days": 1,
            "models": "gfs_seamless",
            "timezone": "UTC",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "monitor-efc/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    hourly = payload.get("hourly") or {}
    precip = hourly.get("precipitation") or []
    wind = hourly.get("wind_speed_10m") or []
    soil = hourly.get("soil_moisture_0_to_7cm") or []

    def _sum(vals):
        nums = [float(v) for v in vals if v is not None]
        return float(sum(nums)) if nums else None

    def _mean(vals):
        nums = [float(v) for v in vals if v is not None]
        return float(sum(nums) / len(nums)) if nums else None

    return {
        "precip_mm": _sum(precip[:24]),
        "wind_ms": _mean(wind[:24]),
        "soil_moisture": _mean(soil[:24]),
    }


def climate_gfs_points(*, step_km: int | None = None, max_points: int = 40) -> list[dict[str, Any]]:
    """Pontos GFS ao longo da EFC (cache curto)."""
    import time

    step = int(step_km or os.environ.get("MONITOR_GFS_LAYER_STEP_KM", "30") or "30")
    key = f"gfs_{step}_{max_points}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return list(hit[1])

    samples = _railway_sample(step)
    if len(samples) > max_points:
        stride = max(1, len(samples) // max_points)
        samples = samples[::stride][:max_points]

    points: list[dict[str, Any]] = []
    for s in samples:
        try:
            met = _fetch_open_meteo(s["lat"], s["lon"])
        except Exception as exc:
            logger.debug("gfs sample km%s: %s", s.get("km"), exc)
            met = {"precip_mm": None, "wind_ms": None, "soil_moisture": None}
        points.append({**s, **met})

    _CACHE[key] = (now, points)
    return list(points)


def km_lat_lon(km: float) -> dict[str, float | None]:
    from lib_efc import get_railway_data, position_at_km

    main_line, cum_km, _mk, _lines = get_railway_data()
    if main_line is None or cum_km is None:
        return {"km": float(km), "lat": None, "lon": None}
    pos = position_at_km(float(km), main_line, cum_km)
    if pos is None:
        return {"km": float(km), "lat": None, "lon": None}
    return {"km": float(km), "lat": float(pos[0]), "lon": float(pos[1])}
