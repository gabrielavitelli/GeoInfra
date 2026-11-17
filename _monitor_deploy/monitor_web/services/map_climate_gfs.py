"""Camada clima 2D — GFS (Open-Meteo) em grelha ao longo da EFC, com frames horários.

Retorna nuvem/chuva + vento (u/v) para overlay raster/partículas e replay no Leaflet.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = float(os.environ.get("MONITOR_GFS_LAYER_TTL_SEC", "1800") or "1800")
_FORECAST_DAYS = int(os.environ.get("MONITOR_GFS_FORECAST_DAYS", "2") or "2")
_MAX_POINTS = int(os.environ.get("MONITOR_GFS_LAYER_MAX_POINTS", "28") or "28")


def _railway_sample(step_km: int = 30) -> list[dict[str, Any]]:
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


def _uv_from_speed_dir(speed_ms: float | None, direction_deg: float | None) -> tuple[float, float]:
    """Meteorological wind direction (from) → u (east), v (north) components."""
    if speed_ms is None or direction_deg is None:
        return 0.0, 0.0
    try:
        sp = float(speed_ms)
        deg = float(direction_deg)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if not (math.isfinite(sp) and math.isfinite(deg)):
        return 0.0, 0.0
    rad = math.radians(deg)
    # From-direction: wind blows toward deg+180
    u = -sp * math.sin(rad)
    v = -sp * math.cos(rad)
    return float(u), float(v)


def _fetch_open_meteo_batch(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Pedido multi-ponto Open-Meteo (GFS) com precip/nuvem/vento horários."""
    if not samples:
        return {"times": [], "grid": []}
    lats = ",".join(f"{s['lat']:.4f}" for s in samples)
    lons = ",".join(f"{s['lon']:.4f}" for s in samples)
    qs = urllib.parse.urlencode(
        {
            "latitude": lats,
            "longitude": lons,
            "hourly": "precipitation,cloud_cover,wind_speed_10m,wind_direction_10m",
            "forecast_days": max(1, min(3, _FORECAST_DAYS)),
            "models": "gfs_seamless",
            "timezone": "UTC",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "monitor-efc/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    locs = payload if isinstance(payload, list) else [payload]
    times: list[str] = []
    if locs:
        times = list((locs[0].get("hourly") or {}).get("time") or [])
    grid: list[dict[str, Any]] = []
    for i, s in enumerate(samples):
        loc = locs[i] if i < len(locs) else {}
        hourly = loc.get("hourly") or {}
        precip = list(hourly.get("precipitation") or [])
        cloud = list(hourly.get("cloud_cover") or [])
        ws = list(hourly.get("wind_speed_10m") or [])
        wd = list(hourly.get("wind_direction_10m") or [])
        n = len(times)
        u_series: list[float] = []
        v_series: list[float] = []
        for ti in range(n):
            u, v = _uv_from_speed_dir(
                ws[ti] if ti < len(ws) else None,
                wd[ti] if ti < len(wd) else None,
            )
            u_series.append(u)
            v_series.append(v)
        # Pad series to n
        while len(precip) < n:
            precip.append(0.0)
        while len(cloud) < n:
            cloud.append(0.0)
        while len(ws) < n:
            ws.append(0.0)
        grid.append(
            {
                "km": s["km"],
                "lat": s["lat"],
                "lon": s["lon"],
                "precip": [float(x) if x is not None else 0.0 for x in precip[:n]],
                "cloud": [float(x) if x is not None else 0.0 for x in cloud[:n]],
                "ws": [float(x) if x is not None else 0.0 for x in ws[:n]],
                "u": u_series,
                "v": v_series,
            }
        )
    return {"times": times, "grid": grid}


def _legacy_points_from_frame(grid: list[dict[str, Any]], frame_i: int) -> list[dict[str, Any]]:
    """Compat: pontos do frame atual no formato antigo (círculos)."""
    out = []
    for g in grid:
        pi = frame_i if frame_i < len(g.get("precip") or []) else 0
        precip = (g.get("precip") or [0])[pi]
        cloud = (g.get("cloud") or [0])[pi] if g.get("cloud") else None
        ws = (g.get("ws") or [0])[pi] if g.get("ws") else None
        out.append(
            {
                "km": g["km"],
                "lat": g["lat"],
                "lon": g["lon"],
                "precip_mm": float(precip),  # mm/h no frame (não 24h)
                "precip_mm_h": float(precip),
                "cloud_pct": float(cloud) if cloud is not None else None,
                "wind_ms": float(ws) if ws is not None else None,
                "u": (g.get("u") or [0])[pi],
                "v": (g.get("v") or [0])[pi],
                "soil_moisture": None,
            }
        )
    return out


def climate_gfs_layer(
    *,
    step_km: int | None = None,
    max_points: int | None = None,
) -> dict[str, Any]:
    """Grelha GFS horária (nuvem/chuva/vento) para overlay 2D + replay."""
    step = int(step_km or os.environ.get("MONITOR_GFS_LAYER_STEP_KM", "30") or "30")
    cap = int(max_points or _MAX_POINTS)
    key = f"gfs_layer_{step}_{cap}_{_FORECAST_DAYS}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return dict(hit[1])

    samples = _railway_sample(step)
    if len(samples) > cap:
        stride = max(1, len(samples) // cap)
        samples = samples[::stride][:cap]

    try:
        batch = _fetch_open_meteo_batch(samples)
    except Exception as exc:
        logger.warning("climate_gfs_layer fetch failed: %s", exc)
        batch = {"times": [], "grid": []}

    times = batch.get("times") or []
    grid = batch.get("grid") or []
    # Frame "agora": primeiro horário >= UTC corrente, senão 0
    frame_i = 0
    if times:
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        for i, t in enumerate(times):
            if str(t)[:13] >= now_iso[:13]:
                frame_i = i
                break
        else:
            frame_i = max(0, len(times) - 1)

    frames = [{"i": i, "t": t} for i, t in enumerate(times)]
    points = _legacy_points_from_frame(grid, frame_i)
    payload = {
        "available": bool(grid and times),
        "model": "gfs_seamless",
        "provider": "open-meteo",
        "frame_i": frame_i,
        "frames": frames,
        "grid": grid,
        "points": points,
        "step_km": step,
        "n_points": len(grid),
        "n_frames": len(frames),
    }
    _CACHE[key] = (now, payload)
    return dict(payload)


def climate_gfs_points(*, step_km: int | None = None, max_points: int = 40) -> list[dict[str, Any]]:
    """Compat: pontos do frame atual (API antiga)."""
    layer = climate_gfs_layer(step_km=step_km, max_points=max_points)
    return list(layer.get("points") or [])


def km_lat_lon(km: float) -> dict[str, float | None]:
    from lib_efc import get_railway_data, position_at_km

    main_line, cum_km, _mk, _lines = get_railway_data()
    if main_line is None or cum_km is None:
        return {"km": float(km), "lat": None, "lon": None}
    pos = position_at_km(float(km), main_line, cum_km)
    if pos is None:
        return {"km": float(km), "lat": None, "lon": None}
    return {"km": float(km), "lat": float(pos[0]), "lon": float(pos[1])}
