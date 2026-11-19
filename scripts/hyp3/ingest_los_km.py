#!/usr/bin/env python3
"""HyP3 InSAR → LOS displacement sampled along EFC km → GCS + BigQuery.

Uses Earthdata secrets (``earthdata-user`` / ``earthdata-password``) via gcloud
or env ``EARTHDATA_USERNAME`` / ``EARTHDATA_PASSWORD``.

Prefers existing SUCCEEDED HyP3 jobs covering the sensor/tunnel AOI near
km ~375 (−4.26, −46.53). Converts unwrapped phase → LOS mm and aggregates
**mean** per km-bin per secondary acquisition date.

Example:
  python3 -m scripts.hyp3.ingest_los_km --limit 24 --km-lo 370 --km-hi 380
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

# S1 C-band wavelength (m)
_LAMBDA_M = 0.055465768
_AOI_LAT = -4.26
_AOI_LON = -46.53
_GCS_PREFIX = "gs://efc-clima-data/obs/hyp3/"
_BQ_TABLE = "hyp3_los_km"


def _secret(name: str) -> str:
    env_map = {
        "earthdata-user": "EARTHDATA_USERNAME",
        "earthdata-password": "EARTHDATA_PASSWORD",
    }
    env = os.environ.get(env_map.get(name, name.upper().replace("-", "_")), "").strip()
    if env:
        return env
    return subprocess.check_output(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={name}",
            "--project=climaticsystem",
        ],
        text=True,
    ).strip()


def _unw_to_los_mm(unw_rad: float) -> float:
    # Convention: positive = motion toward satellite (decrease in range).
    return float(-unw_rad * _LAMBDA_M / (4.0 * math.pi) * 1000.0)


def _parse_dates_from_filename(name: str) -> tuple[str | None, str | None]:
    """Extract ref/acq dates from HyP3 product or efc-12d job name."""
    import re

    s = name or ""

    def iso(raw: str) -> str:
        d = re.sub(r"\D", "", raw)[:8]
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

    # S1DD_20260929T084140_20261011T084140_… or …_20260929_20261011_…
    m = re.search(r"(20\d{6})T\d{6}_(20\d{6})T\d{6}", s)
    if not m:
        m = re.search(r"(20\d{6})_(20\d{6})", s)
    if not m:
        # efc-12d-20260929-20261011-p024d-iw3
        m = re.search(r"(20\d{6})-(20\d{6})", s)
    if not m:
        return None, None
    return iso(m.group(1)), iso(m.group(2))


def _railway_km_points(km_lo: float, km_hi: float, step: float = 0.25):
    """Lat/lon samples along official Vale km (SHP+KML estacas)."""
    root = Path(__file__).resolve().parents[2]
    deploy = root / "_monitor_deploy"
    if str(deploy) not in sys.path:
        sys.path.insert(0, str(deploy))
    # Avoid hard xarray dependency when only railway geom is needed.
    if "xarray" not in sys.modules:
        import types

        sys.modules["xarray"] = types.ModuleType("xarray")
    from lib_efc import get_railway_data, position_at_km

    main, cum, _, _ = get_railway_data()
    if main is None or cum is None:
        raise RuntimeError("railway geometry unavailable (need data/EFC.kmz.kml)")
    pts = []
    km = float(km_lo)
    while km <= float(km_hi) + 1e-9:
        pos = position_at_km(km, main, cum)
        if pos is not None:
            pts.append((round(km, 2), float(pos[0]), float(pos[1])))
        km += step
    return pts


def _covers_aoi(ds, lon: float = _AOI_LON, lat: float = _AOI_LAT) -> bool:
    from rasterio.warp import transform as T

    xx, yy = T("EPSG:4326", ds.crs, [lon], [lat])
    return (
        ds.bounds.left <= xx[0] <= ds.bounds.right
        and ds.bounds.bottom <= yy[0] <= ds.bounds.top
    )


def _sample_mean_by_km(
    tif_path: Path,
    km_pts: list[tuple[float, float, float]],
    *,
    corr_path: Path | None = None,
    radius_m: float = 500.0,
    gamma_min: float = 0.30,
    ring_inner_m: float = 0.0,
) -> list[dict]:
    """Mean LOS in a circular disk (no ring when ``ring_inner_m=0``).

    Pixels must satisfy coherence γ ≥ ``gamma_min`` when a corr.tif is present.
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    from rasterio.warp import transform as T

    out: list[dict] = []
    r_out = max(1.0, float(radius_m))
    r_in = max(0.0, float(ring_inner_m))
    g_min = float(gamma_min)

    with rasterio.open(tif_path) as ds:
        nodata = ds.nodata
        # Pixel size in CRS units (HyP3 UTM → metres).
        px = abs(float(ds.transform.a)) or 1.0
        py = abs(float(ds.transform.e)) or px
        pix_m = 0.5 * (px + py)
        pad = int(math.ceil(r_out / max(pix_m, 1e-6))) + 2

        corr_ds = rasterio.open(corr_path) if corr_path and corr_path.is_file() else None
        try:
            for km, lat, lon in km_pts:
                xx, yy = T("EPSG:4326", ds.crs, [lon], [lat])
                if not (
                    ds.bounds.left <= xx[0] <= ds.bounds.right
                    and ds.bounds.bottom <= yy[0] <= ds.bounds.top
                ):
                    continue
                row, col = ds.index(xx[0], yy[0])
                r0, r1 = max(0, row - pad), min(ds.height, row + pad + 1)
                c0, c1 = max(0, col - pad), min(ds.width, col + pad + 1)
                if r1 <= r0 or c1 <= c0:
                    continue
                win = Window.from_slices((r0, r1), (c0, c1))
                unw = ds.read(1, window=win).astype("float64")
                if nodata is not None:
                    unw = np.where(unw == nodata, np.nan, unw)
                unw = np.where(np.isfinite(unw), unw, np.nan)

                # Distance grid in metres from the km point (CRS metres).
                rows = np.arange(r0, r1)[:, None]
                cols = np.arange(c0, c1)[None, :]
                xs = ds.transform.c + (cols + 0.5) * ds.transform.a
                ys = ds.transform.f + (rows + 0.5) * ds.transform.e
                dist = np.hypot(xs - xx[0], ys - yy[0])
                mask = (dist <= r_out) & (dist >= r_in) & np.isfinite(unw)

                if corr_ds is not None:
                    corr = corr_ds.read(1, window=win).astype("float64")
                    c_nd = corr_ds.nodata
                    if c_nd is not None:
                        corr = np.where(corr == c_nd, np.nan, corr)
                    mask &= np.isfinite(corr) & (corr >= g_min)

                if not mask.any():
                    continue
                vals = unw[mask]
                mean_unw = float(np.mean(vals))
                out.append(
                    {
                        "km": float(km),
                        "lat": lat,
                        "lon": lon,
                        "unw_rad": mean_unw,
                        "los_mm": _unw_to_los_mm(mean_unw),
                        "n_pix": int(mask.sum()),
                        "gamma_min": g_min,
                        "radius_m": r_out,
                        "ring_inner_m": r_in,
                    }
                )
        finally:
            if corr_ds is not None:
                corr_ds.close()
    return out


def _select_jobs(
    hyp3,
    *,
    limit: int,
    name_substr: str = "p024d-iw3",
    max_baseline_days: int | None = 18,
):
    jobs = []
    for j in hyp3.find_jobs():
        if getattr(j, "status_code", None) != "SUCCEEDED":
            continue
        name = j.name or ""
        if name_substr and name_substr not in name:
            continue
        if not j.files:
            continue
        if max_baseline_days is not None:
            fn = (j.files or [{}])[0].get("filename") or ""
            ref_d, acq_d = _parse_dates_from_filename(fn) or (None, None)
            if not acq_d:
                ref_d, acq_d = _parse_dates_from_filename(name)
            if ref_d and acq_d:
                try:
                    from datetime import date as _date

                    d0 = _date.fromisoformat(ref_d)
                    d1 = _date.fromisoformat(acq_d)
                    if abs((d1 - d0).days) > int(max_baseline_days):
                        continue
                except Exception:
                    pass
        jobs.append(j)
    jobs.sort(key=lambda j: j.request_time or "", reverse=True)
    return jobs[: max(1, limit)]


def _upload_json(local: Path, gcs_uri: str) -> None:
    subprocess.check_call(["gsutil", "-q", "cp", str(local), gcs_uri])


def _ensure_bq_table(project: str, dataset: str, table: str) -> None:
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    full = f"{project}.{dataset}.{table}"
    schema = [
        bigquery.SchemaField("acq_date", "DATE"),
        bigquery.SchemaField("ref_date", "DATE"),
        bigquery.SchemaField("km", "FLOAT"),
        bigquery.SchemaField("los_mm", "FLOAT"),
        bigquery.SchemaField("los_mm_cum", "FLOAT"),
        bigquery.SchemaField("unw_rad", "FLOAT"),
        bigquery.SchemaField("job_id", "STRING"),
        bigquery.SchemaField("job_name", "STRING"),
        bigquery.SchemaField("product", "STRING"),
        bigquery.SchemaField("path", "INT64"),
        bigquery.SchemaField("aggregation", "STRING"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]
    try:
        client.get_table(full)
    except Exception:
        tbl = bigquery.Table(full, schema=schema)
        client.create_table(tbl)


def _load_bq(rows: list[dict], project: str, dataset: str, table: str) -> int:
    if not rows:
        return 0
    from google.cloud import bigquery

    _ensure_bq_table(project, dataset, table)
    client = bigquery.Client(project=project)
    full = f"{project}.{dataset}.{table}"
    # Delete overlapping job_ids then append.
    job_ids = sorted({r["job_id"] for r in rows if r.get("job_id")})
    if job_ids:
        q = f"DELETE FROM `{full}` WHERE job_id IN UNNEST(@ids)"
        job_cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", job_ids)]
        )
        try:
            client.query(q, job_config=job_cfg).result()
        except Exception:
            pass
    errors = client.insert_rows_json(full, rows)
    if errors:
        raise RuntimeError(f"BQ insert errors: {errors[:3]}")
    return len(rows)


def _add_cumulative(rows: list[dict]) -> list[dict]:
    """Per-km cumulative LOS relative to first acquisition date (mean baseline)."""
    by_km: dict[float, list[dict]] = {}
    for r in rows:
        by_km.setdefault(float(r["km"]), []).append(r)
    out: list[dict] = []
    for km, items in by_km.items():
        items = sorted(items, key=lambda x: str(x.get("acq_date") or ""))
        if not items:
            continue
        running = 0.0
        # Pairwise products are already relative; treat each pair's los_mm as
        # incremental (SBAS-lite cumulative).
        for i, r in enumerate(items):
            rr = dict(r)
            if i == 0:
                rr["los_mm_cum"] = 0.0
            else:
                running += float(r.get("los_mm") or 0.0)
                rr["los_mm_cum"] = round(running, 4)
            out.append(rr)
    return out


def submit_basic_job(hyp3, *, name: str = "efc-tunnel-km375-basic") -> Any:
    """Kick off HyP3 INSAR_GAMMA (Basic) with LOS displacement for AOI pair."""
    import asf_search as asf

    results = asf.geo_search(
        platform=[asf.PLATFORM.SENTINEL1],
        processingLevel=[asf.PRODUCT_TYPE.SLC],
        intersectsWith=f"POINT({_AOI_LON} {_AOI_LAT})",
        start="2026-09-01",
        end="2026-11-19",
        maxResults=20,
    )
    # Prefer path 24 descending pairs with ~12–24 day baseline.
    by_path: dict[int, list] = {}
    for r in results:
        p = int(r.properties.get("pathNumber") or 0)
        by_path.setdefault(p, []).append(r)
    path = 24 if 24 in by_path else (next(iter(by_path)) if by_path else None)
    if path is None:
        raise RuntimeError("no SLC granules near AOI")
    scenes = sorted(by_path[path], key=lambda r: r.properties["startTime"])
    if len(scenes) < 2:
        raise RuntimeError("need ≥2 SLC scenes")
    g1 = scenes[-2].properties["sceneName"]
    g2 = scenes[-1].properties["sceneName"]
    job = hyp3.submit_insar_job(
        granule1=g1,
        granule2=g2,
        name=name,
        include_look_vectors=False,
        include_los_displacement=True,
        include_displacement_maps=True,
        apply_water_mask=True,
        looks="20x4",
    )
    return job


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--km-lo", type=float, default=370.0)
    ap.add_argument("--km-hi", type=float, default=380.0)
    ap.add_argument("--km-step", type=float, default=0.5)
    ap.add_argument(
        "--name-substr",
        default="p024d-iw3",
        help="Job name filter; combined with --max-baseline-days for short pairs",
    )
    ap.add_argument(
        "--max-baseline-days",
        type=int,
        default=18,
        help="Skip HyP3 pairs with |acq-ref| days above this (0 = no filter)",
    )
    ap.add_argument("--gcs-prefix", default=_GCS_PREFIX)
    ap.add_argument("--bq-project", default="climaticsystem")
    ap.add_argument("--bq-dataset", default="efc_clima")
    ap.add_argument("--bq-table", default=_BQ_TABLE)
    ap.add_argument("--submit-basic", action="store_true")
    ap.add_argument("--skip-bq", action="store_true")
    ap.add_argument("--gamma-min", type=float, default=0.30, help="Coherence γ floor (corr.tif)")
    ap.add_argument("--radius-m", type=float, default=500.0, help="Outer disk radius (m)")
    ap.add_argument(
        "--ring-inner-m",
        type=float,
        default=0.0,
        help="Inner hole radius (m); 0 = disco sem anel",
    )
    args = ap.parse_args(argv)
    agg_label = (
        f"mean_disk_{args.radius_m:g}m_gamma_ge_{args.gamma_min:g}"
        + (f"_ring_in_{args.ring_inner_m:g}m" if args.ring_inner_m > 0 else "_no_ring")
    )

    user = _secret("earthdata-user")
    pwd = _secret("earthdata-password")
    from hyp3_sdk import HyP3

    hyp3 = HyP3(username=user, password=pwd)
    print(f"hyp3 ok user_len={len(user)}")

    if args.submit_basic:
        try:
            job = submit_basic_job(hyp3)
            print("submitted_basic", getattr(job, "job_id", job))
        except Exception as exc:
            print("submit_basic_failed", type(exc).__name__, str(exc)[:300])

    km_pts = _railway_km_points(args.km_lo, args.km_hi, step=args.km_step)
    print(f"km_pts={len(km_pts)} range={args.km_lo}-{args.km_hi}")

    max_bl = None if int(args.max_baseline_days) <= 0 else int(args.max_baseline_days)
    jobs = _select_jobs(
        hyp3,
        limit=max(args.limit * 5, args.limit),
        name_substr=args.name_substr,
        max_baseline_days=max_bl,
    )
    print(
        f"candidate_jobs={len(jobs)} substr={args.name_substr!r} "
        f"max_bl={max_bl} agg={agg_label}"
    )

    all_rows: list[dict] = []
    used = 0
    with tempfile.TemporaryDirectory(prefix="hyp3_efc_") as td:
        tdir = Path(td)
        for j in jobs:
            if used >= args.limit:
                break
            url = (j.files or [{}])[0].get("url")
            fname = (j.files or [{}])[0].get("filename") or ""
            if not url:
                continue
            zpath = tdir / f"{j.job_id}.zip"
            try:
                urlretrieve(url, zpath)
            except Exception as exc:
                print("download_fail", j.name, str(exc)[:120])
                continue
            try:
                with zipfile.ZipFile(zpath) as zf:
                    names = zf.namelist()
                    unw_names = [n for n in names if n.endswith("unw_phase.tif")]
                    corr_names = [n for n in names if n.endswith("corr.tif")]
                    if not unw_names:
                        print("no_unw", j.name)
                        continue
                    dest = tdir / j.job_id
                    zf.extract(unw_names[0], dest)
                    tif = dest / unw_names[0]
                    corr_tif = None
                    if corr_names:
                        zf.extract(corr_names[0], dest)
                        corr_tif = dest / corr_names[0]
            except Exception as exc:
                print("unzip_fail", j.name, str(exc)[:120])
                continue
            import rasterio

            with rasterio.open(tif) as ds:
                if not _covers_aoi(ds):
                    print("skip_bounds", j.name)
                    continue
            samples = _sample_mean_by_km(
                tif,
                km_pts,
                corr_path=corr_tif,
                radius_m=args.radius_m,
                gamma_min=args.gamma_min,
                ring_inner_m=args.ring_inner_m,
            )
            if not samples:
                print("no_samples", j.name)
                continue
            ref_d, acq_d = _parse_dates_from_filename(fname)
            if not acq_d:
                ref_d, acq_d = _parse_dates_from_filename(unw_names[0] if unw_names else "")
            if not acq_d:
                ref_d, acq_d = _parse_dates_from_filename(j.name or "")
            now = datetime.now(timezone.utc).isoformat()
            for s in samples:
                all_rows.append(
                    {
                        "acq_date": acq_d,
                        "ref_date": ref_d,
                        "km": s["km"],
                        "los_mm": round(float(s["los_mm"]), 4),
                        "los_mm_cum": None,
                        "unw_rad": round(float(s["unw_rad"]), 6),
                        "job_id": j.job_id,
                        "job_name": j.name,
                        "product": fname,
                        "path": 24,
                        "aggregation": agg_label,
                        "ingested_at": now,
                    }
                )
            used += 1
            print(
                "ok",
                used,
                j.name,
                "acq",
                acq_d,
                "n_km",
                len(samples),
                "mean_los",
                round(sum(s["los_mm"] for s in samples) / len(samples), 3),
            )

    all_rows = _add_cumulative(all_rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    local_json = Path("/tmp") / f"hyp3_los_km_{stamp}.json"
    payload = {
        "generated_at": stamp,
        "aoi": {"lat": _AOI_LAT, "lon": _AOI_LON, "km_lo": args.km_lo, "km_hi": args.km_hi},
        "aggregation": agg_label,
        "method": {
            "gamma_min": args.gamma_min,
            "radius_m": args.radius_m,
            "ring_inner_m": args.ring_inner_m,
            "shape": "disk" if args.ring_inner_m <= 0 else "ring",
        },
        "unit": "mm LOS",
        "n_rows": len(all_rows),
        "n_jobs": used,
        "rows": all_rows,
    }
    local_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    latest = args.gcs_prefix.rstrip("/") + "/hyp3_los_km_latest.json"
    stamped = args.gcs_prefix.rstrip("/") + f"/hyp3_los_km_{stamp}.json"
    _upload_json(local_json, latest)
    _upload_json(local_json, stamped)
    print("gcs", latest, "rows", len(all_rows))

    if not args.skip_bq and all_rows:
        n = _load_bq(all_rows, args.bq_project, args.bq_dataset, args.bq_table)
        print("bq", f"{args.bq_project}.{args.bq_dataset}.{args.bq_table}", n)
    return 0 if all_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
