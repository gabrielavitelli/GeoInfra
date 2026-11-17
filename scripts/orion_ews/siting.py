"""Where Orion loggers sit vs the EFC — corridor vs a single yard.

EFC (Carajás) runs ~São Luís (km 0) → Carajás (~km 890). Field names like
``Abrigo km 629`` are chainage on that line. A cluster of coords in one
bbox of a few km would mean a pátio/obra, not the railway.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

from scripts.orion_ews.mapping import km_hint_from_name, station_code_from_device

# Loose envelope around the Carajás railway (deg). Not a precise shapefile.
EFC_LAT = (-6.9, -2.0)
EFC_LON = (-51.2, -43.6)
CLUSTER_KM = 15.0  # all points inside this → "um ponto / pátio"


def _hav_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _in_efc(lat: float, lon: float) -> bool:
    return EFC_LAT[0] <= lat <= EFC_LAT[1] and EFC_LON[0] <= lon <= EFC_LON[1]


def siting_report(devices: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Classify Orion fleet siting for inspection dispatch."""
    pts: list[dict[str, Any]] = []
    kms: list[int] = []
    for d in devices:
        name = str(d.get("deviceName") or d.get("name") or "")
        km = km_hint_from_name(name)
        if km is not None:
            kms.append(km)
        lat, lon = d.get("latitude"), d.get("longitude")
        try:
            lat_f, lon_f = float(lat), float(lon)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not math.isfinite(lat_f) or not math.isfinite(lon_f):
            continue
        if abs(lat_f) < 1e-6 and abs(lon_f) < 1e-6:
            continue
        pts.append(
            {
                "station_id": station_code_from_device(d),
                "name": name,
                "lat": lat_f,
                "lon": lon_f,
                "km": km,
                "in_efc_bbox": _in_efc(lat_f, lon_f),
            }
        )

    n = len(pts)
    in_bbox = sum(1 for p in pts if p["in_efc_bbox"])
    span_km_name: Optional[int] = (max(kms) - min(kms)) if kms else None
    max_pair_km = 0.0
    if n >= 2:
        for i, a in enumerate(pts):
            for b in pts[i + 1 :]:
                max_pair_km = max(max_pair_km, _hav_km(a["lat"], a["lon"], b["lat"], b["lon"]))

    if n == 0:
        pattern = "unknown"
        verdict = "Sem coordenadas no catálogo Orion — conferir lat/lon no portal."
    elif n == 1 and (span_km_name or 0) < 20:
        pattern = "point"
        verdict = "Um único ponto com coordenada. Não dá para afirmar o corredor inteiro."
    elif (span_km_name or 0) >= 80 or max_pair_km >= CLUSTER_KM * 3:
        pattern = "corridor"
        verdict = (
            "Loggers espalhados ao longo da EFC (abrigos por km), não um pátio só. "
            "Inspeção: filtrar por km + status (online/atrasado/parado)."
        )
    elif max_pair_km <= CLUSTER_KM:
        pattern = "cluster"
        verdict = (
            "Pontos concentrados (~%.0f km entre extremos) — pátio/obra, não o corredor todo."
            % max_pair_km
        )
    else:
        pattern = "corridor"
        verdict = "Distribuição alongada, compatível com a ferrovia."

    return {
        "pattern": pattern,
        "verdict_pt": verdict,
        "n_devices": len(devices),
        "n_with_coords": n,
        "n_in_efc_bbox": in_bbox,
        "pct_in_efc_bbox": round(100.0 * in_bbox / n, 1) if n else None,
        "km_min": min(kms) if kms else None,
        "km_max": max(kms) if kms else None,
        "km_span": span_km_name,
        "max_pair_km": round(max_pair_km, 1),
        "bbox": {
            "lat_min": min((p["lat"] for p in pts), default=None),
            "lat_max": max((p["lat"] for p in pts), default=None),
            "lon_min": min((p["lon"] for p in pts), default=None),
            "lon_max": max((p["lon"] for p in pts), default=None),
        },
        "efc_envelope": {"lat": list(EFC_LAT), "lon": list(EFC_LON)},
    }
