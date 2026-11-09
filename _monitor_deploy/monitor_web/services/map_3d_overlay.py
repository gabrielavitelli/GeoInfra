"""GeoJSON leve para MapLibre 3D: ferrovia, estações, túneis/cortes."""
from __future__ import annotations

import json
import math
from typing import Any

# Cortes rochosos (ativos Vale) + trechos tratados como túnel visual no 3D.
_TUNNEL_KM_RANGES: tuple[tuple[float, float, str], ...] = (
    (372.56, 373.535, "Corte rochoso"),
    (374.085, 375.2, "Corte rochoso"),
)


def _downsample_line(main_line: list, *, max_pts: int = 900) -> list[list[float]]:
    if not main_line:
        return []
    n = len(main_line)
    step = max(1, int(math.ceil(n / max(2, max_pts))))
    coords = [[float(p[1]), float(p[0])] for p in main_line[::step]]
    last = main_line[-1]
    if coords[-1] != [float(last[1]), float(last[0])]:
        coords.append([float(last[1]), float(last[0])])
    return coords


def _station_features(dfs: list, *, net: str, color: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for df in dfs:
        if df is None or getattr(df, "empty", True):
            continue
        lat_col = next((c for c in ("lat", "latitude", "Latitude") if c in df.columns), None)
        lon_col = next((c for c in ("lon", "longitude", "Longitude", "lng") if c in df.columns), None)
        if not lat_col or not lon_col:
            continue
        code_col = next(
            (
                c
                for c in (
                    "codEstacao",
                    "codigo",
                    "code",
                    "id",
                    "station_id",
                    "cod_estacao",
                )
                if c in df.columns
            ),
            None,
        )
        name_col = next(
            (c for c in ("nome", "name", "station_name", "municipio") if c in df.columns),
            None,
        )
        for _, row in df.iterrows():
            try:
                lat = float(row[lat_col])
                lon = float(row[lon_col])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
            code = str(row[code_col]).strip() if code_col is not None else ""
            key = f"{net}:{code or round(lat, 4)}:{round(lon, 4)}"
            if key in seen:
                continue
            seen.add(key)
            name = str(row[name_col]).strip() if name_col is not None else ""
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "net": net,
                        "code": code,
                        "name": name or code or net,
                        "color": color,
                    },
                }
            )
    return out


def _tunnel_features(main_line, cum_km) -> list[dict[str, Any]]:
    from lib_efc import position_at_km

    feats: list[dict[str, Any]] = []
    if main_line is None or cum_km is None:
        return feats
    for km0, km1, label in _TUNNEL_KM_RANGES:
        coords: list[list[float]] = []
        step = 0.12
        km = float(km0)
        while km <= float(km1) + 1e-9:
            pos = position_at_km(km, main_line, cum_km)
            if pos is not None:
                coords.append([float(pos[1]), float(pos[0])])
            km += step
        if len(coords) < 2:
            continue
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"label": label, "kind": "tunnel"},
            }
        )
        for pt in (coords[0], coords[-1]):
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": pt},
                    "properties": {"label": "Túnel", "kind": "portal"},
                }
            )
    return feats


def _km_label_features(main_line, cum_km, *, step_km: int = 50) -> list[dict[str, Any]]:
    """Marcos km ao longo da ferrovia (sempre visíveis no 3D)."""
    from lib_efc import position_at_km

    feats: list[dict[str, Any]] = []
    if main_line is None or cum_km is None:
        return feats
    km_max = float(cum_km[-1])
    step = max(25, int(step_km))
    km = 0.0
    while km <= km_max + 1e-6:
        pos = position_at_km(km, main_line, cum_km)
        if pos is not None:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(pos[1]), float(pos[0])]},
                    "properties": {"km": int(round(km)), "label": f"km {int(round(km))}"},
                }
            )
        km += step
    # Garante o fim da linha
    end_km = int(round(km_max))
    if not feats or int(feats[-1]["properties"]["km"]) != end_km:
        pos = position_at_km(float(end_km), main_line, cum_km)
        if pos is not None:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(pos[1]), float(pos[0])]},
                    "properties": {"km": end_km, "label": f"km {end_km}"},
                }
            )
    return feats


def _cinematic_camera(main_line, cum_km) -> dict[str, float] | None:
    """Ângulo inicial que valoriza relevo + traçado da ferrovia (trecho médio)."""
    from lib_efc import position_at_km

    if main_line is None or cum_km is None:
        return None
    km_max = float(cum_km[-1])
    focus_km = min(380.0, max(180.0, km_max * 0.42))
    pos = position_at_km(focus_km, main_line, cum_km)
    if pos is None:
        return None
    return {
        "lat": float(pos[0]),
        "lon": float(pos[1]),
        "zoom": 6.55,
        "pitch": 72.0,
        "bearing": -42.0,
        "km": float(focus_km),
    }


def build_map_3d_payload(
    *,
    main_line,
    cum_km,
    estacoes_df=None,
    ana_estacoes_df=None,
    orion_estacoes_df=None,
) -> dict[str, Any]:
    rail_coords = _downsample_line(main_line or [], max_pts=900)
    bounds = None
    if rail_coords:
        lons = [c[0] for c in rail_coords]
        lats = [c[1] for c in rail_coords]
        pad = 0.18
        bounds = [
            [min(lons) - pad, min(lats) - pad],
            [max(lons) + pad, max(lats) + pad],
        ]
    stations = []
    stations.extend(_station_features([estacoes_df], net="BNDMET", color="#34d399"))
    stations.extend(_station_features([ana_estacoes_df], net="ANA", color="#38bdf8"))
    stations.extend(_station_features([orion_estacoes_df], net="EWS", color="#fbbf24"))
    tunnels = _tunnel_features(main_line, cum_km)
    km_labels = _km_label_features(main_line, cum_km, step_km=50)
    camera = _cinematic_camera(main_line, cum_km)
    return {
        "rail": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": rail_coords},
                    "properties": {"name": "EFC"},
                }
            ]
            if rail_coords
            else [],
        },
        "stations": {"type": "FeatureCollection", "features": stations},
        "tunnels": {"type": "FeatureCollection", "features": tunnels},
        "kmLabels": {"type": "FeatureCollection", "features": km_labels},
        "bounds": bounds,
        "camera": camera,
    }


def map_3d_payload_json(**kwargs) -> str:
    return json.dumps(build_map_3d_payload(**kwargs), separators=(",", ":"), ensure_ascii=False)
