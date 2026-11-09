#!/usr/bin/env python3
"""Refresh boletim ~2 m imagery per EFC km into GCS (Esri World Imagery export).

Usa tiles Esri World Imagery (resolução típica ~0.5–2 m). Sem bucket NODIS
proprietário disponível — esta é a fonte operacional de alta resolução.

Uso:
  python scripts/refresh_boletim_km_imagery.py --step 5 --km-max 877
  python scripts/refresh_boletim_km_imagery.py --km 629
  EFC_BOLETIM_IMAGERY_GCS=gs://efc-clima-data/monitor/boletim-imagery \\
    python scripts/refresh_boletim_km_imagery.py --step 10
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
DEFAULT_GCS = "gs://efc-clima-data/monitor/boletim-imagery"
Z = int(os.environ.get("EFC_BOLETIM_IMAGERY_ZOOM", "16") or "16")
PATCH = int(os.environ.get("EFC_BOLETIM_IMAGERY_PATCH", "3") or "3")  # tiles around center


def _latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.0511), -85.0511)
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _fetch_tile(z: int, x: int, y: int) -> bytes:
    url = TILE.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "monitor-efc-boletim-imagery/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _compose_patch(lat: float, lon: float, zoom: int = Z, patch: int = PATCH) -> bytes:
    from PIL import Image

    cx, cy = _latlon_to_tile(lat, lon, zoom)
    tiles = []
    for dy in range(-patch, patch + 1):
        row = []
        for dx in range(-patch, patch + 1):
            raw = _fetch_tile(zoom, cx + dx, cy + dy)
            row.append(Image.open(io.BytesIO(raw)).convert("RGB"))
            time.sleep(0.05)
        tiles.append(row)
    tw, th = tiles[0][0].size
    out = Image.new("RGB", (tw * len(tiles[0]), th * len(tiles)))
    for iy, row in enumerate(tiles):
        for ix, im in enumerate(row):
            out.paste(im, (ix * tw, iy * th))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _upload_gcs(path_uri: str, data: bytes, content_type: str) -> None:
    from google.cloud import storage

    assert path_uri.startswith("gs://")
    rest = path_uri[5:]
    bucket_name, _, blob_name = rest.partition("/")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.cache_control = "public, max-age=86400"
    blob.upload_from_string(data, content_type=content_type)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--km-max", type=int, default=877)
    ap.add_argument("--km", type=int, default=None, help="Refresh um único km")
    ap.add_argument("--gcs", default=os.environ.get("EFC_BOLETIM_IMAGERY_GCS", DEFAULT_GCS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from lib_efc import KMZ_PATH, get_railway_data, position_at_km

    main_line, cum_km, _mk, _lines = get_railway_data()
    if main_line is None or cum_km is None:
        # Shapefile precisa geopandas; tenta KMZ e depois BQ railway_km.
        main_line, cum_km, _mk, _lines = get_railway_data(KMZ_PATH)

    def _pos_bq(km: int) -> tuple[float, float] | None:
        try:
            from google.cloud import bigquery

            client = bigquery.Client()
            sql = """
            SELECT lat, lon
            FROM `{}.{}.locations`
            WHERE source = 'railway_km' AND CAST(ROUND(km_efc) AS INT64) = @km
            LIMIT 1
            """.format(
                client.project, os.environ.get("BQ_DATASET", "efc_clima")
            )
            rows = list(
                client.query(
                    sql,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ScalarQueryParameter("km", "INT64", int(km))]
                    ),
                ).result()
            )
            if not rows:
                return None
            return float(rows[0].lat), float(rows[0].lon)
        except Exception as exc:
            print(f"bq fallback km {km}: {exc}", file=sys.stderr)
            return None

    kms = [args.km] if args.km is not None else list(range(0, args.km_max + 1, max(1, args.step)))
    prefix = args.gcs.rstrip("/")
    ok = 0
    for km in kms:
        pos = None
        if main_line is not None and cum_km is not None:
            pos = position_at_km(float(km), main_line, cum_km)
        if pos is None:
            pos = _pos_bq(int(km))
        if pos is None:
            print(f"skip km {km}: no position")
            continue
        lat, lon = float(pos[0]), float(pos[1])
        print(f"km {km}: lat={lat:.5f} lon={lon:.5f}")
        if args.dry_run:
            ok += 1
            continue
        try:
            jpg = _compose_patch(lat, lon)
            meta = {
                "km": int(km),
                "lat": lat,
                "lon": lon,
                "zoom": Z,
                "patch": PATCH,
                "resolution_m_approx": 2,
                "source": "Esri World Imagery",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _upload_gcs(f"{prefix}/km_{int(km):04d}.jpg", jpg, "image/jpeg")
            _upload_gcs(
                f"{prefix}/km_{int(km):04d}.json",
                json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
            )
            try:
                from google.cloud import storage as gcs

                rest = f"{prefix}/km_{int(km):04d}.jpg"[5:]
                bkt, _, blob_name = rest.partition("/")
                gcs.Client().bucket(bkt).blob(blob_name).make_public()
            except Exception:
                pass
            ok += 1
            print(f"  uploaded ({len(jpg)} bytes)")
        except Exception as exc:
            print(f"  FAIL km {km}: {exc}", file=sys.stderr)
    print(f"done: {ok}/{len(kms)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
