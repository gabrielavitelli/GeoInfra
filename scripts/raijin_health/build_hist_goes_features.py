#!/usr/bin/env python3
"""
Baixa GOES MCMIPC histórico (streaming) e extrai features nos km EWS.

Modo padrão: dias de evento UNIPLU (≥49 mm) + amostra de negativos no
intervalo --start/--end, 4 horários UTC (0/6/12/18). Baixa → extrai → apaga
NetCDF (disco ~200 MB pico).

Uso:
  python3 scripts/raijin_health/build_hist_goes_features.py \\
    --start 2024-10-01 --end 2025-12-31 \\
    --out ~/raijin_eval/features_goes_hist.csv \\
    --kms-from ~/raijin_eval/../  # ou lista --kms 51,149,...
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# logs em tempo real (tmux/pipe)
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass

PROJECT = os.environ.get("GCP_PROJECT", "climaticsystem")
BUCKET_GCS = os.environ.get("GCS_BUCKET", "efc-clima-data")
FEATURES_BLOB = "ml/raijin-trainkit/data/ml/cascade_v2/features_cascade_v2.csv"
PRODUCT = "ABI-L2-MCMIPC"
HOURS = (0, 6, 12, 18)
THRESHOLD_MM = 49.0
_CMI_BAND_RE = re.compile(r"CMI_C(\d{2})", re.I)

GOES_BUCKETS_BY_ERA = (
    (date(2017, 12, 18), date(2024, 12, 31), "noaa-goes16"),
    (date(2025, 1, 1), date(2025, 12, 31), "noaa-goes18"),
    (date(2026, 1, 1), date(2099, 12, 31), "noaa-goes19"),
)
FALLBACKS = ("noaa-goes19", "noaa-goes18", "noaa-goes16")


def _bucket_for(d: date) -> str:
    for a, b, name in GOES_BUCKETS_BY_ERA:
        if a <= d <= b:
            return name
    return FALLBACKS[0]


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=PROJECT)


def _load_kms(args: argparse.Namespace) -> dict[int, tuple[float, float]]:
    from google.cloud import storage

    dest = Path(tempfile.gettempdir()) / "features_cascade_v2_kms.csv"
    if not dest.exists() or dest.stat().st_size == 0:
        storage.Client(project=PROJECT).bucket(BUCKET_GCS).blob(FEATURES_BLOB).download_to_filename(
            str(dest)
        )
    df = pd.read_csv(dest)
    want = (
        {int(x) for x in args.kms.split(",") if x.strip()}
        if args.kms
        else set(df["km_efc"].dropna().astype(int).unique())
    )
    coords_csv = {}
    if {"km_efc", "lat", "lon"}.issubset(df.columns):
        for r in df.dropna(subset=["km_efc", "lat", "lon"]).itertuples(index=False):
            k = int(r.km_efc)
            if k in want:
                coords_csv[k] = (float(r.lat), float(r.lon))
    if len(coords_csv) >= max(1, len(want) // 2):
        return {k: coords_csv[k] for k in want if k in coords_csv}

    sql = f"""
    SELECT CAST(ROUND(km_efc) AS INT64) AS km_efc, lat, lon
    FROM `{PROJECT}.efc_clima.locations`
    WHERE source = 'railway_km' AND km_efc IS NOT NULL AND lat IS NOT NULL
    """
    loc = _bq().query(sql).to_dataframe(create_bqstorage_client=False)
    out = {
        int(r.km_efc): (float(r.lat), float(r.lon))
        for r in loc.itertuples(index=False)
        if int(r.km_efc) in want
    }
    missing = want - set(out)
    if missing:
        print(f"AVISO: kms sem coords: {sorted(missing)}")
    return out


def _uniplu_event_days(d0: date, d1: date, catalog: Path) -> tuple[set[date], set[date]]:
    """Retorna (event_days, all_summer_days) a partir de UNIPLU no BQ."""
    from google.cloud import bigquery

    if not catalog.is_file():
        print(f"Catálogo UNIPLU ausente: {catalog}")
        return set(), set()
    cat = pd.read_csv(catalog)
    locs = [f"uniplu_{c}" for c in cat["cod"].astype(str).unique()]
    sql = f"""
    SELECT date AS day, MAX(value) AS mm
    FROM `{PROJECT}.efc_clima.clima_series`
    WHERE source = 'uniplu' AND variable = 'precip_mm'
      AND date BETWEEN @d0 AND @d1
      AND location_id IN UNNEST(@locs)
      AND EXTRACT(MONTH FROM date) IN (10,11,12,1,2,3,4,5)
    GROUP BY date
    """
    df = _bq().query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d0", "DATE", d0.isoformat()),
                bigquery.ScalarQueryParameter("d1", "DATE", d1.isoformat()),
                bigquery.ArrayQueryParameter("locs", "STRING", locs),
            ]
        ),
    ).to_dataframe(create_bqstorage_client=False)
    if df.empty:
        return set(), set()
    df["day"] = pd.to_datetime(df["day"]).dt.date
    all_days = set(df["day"])
    events = set(df.loc[df["mm"] >= THRESHOLD_MM, "day"])
    return events, all_days


def _pick_days(events: set[date], all_days: set[date], neg_ratio: float, seed: int) -> list[date]:
    rng = np.random.default_rng(seed)
    negs = sorted(all_days - events)
    n_neg = min(len(negs), max(1, int(round(len(events) * neg_ratio)))) if events else len(negs)
    if n_neg and negs:
        chosen_neg = list(rng.choice(negs, size=n_neg, replace=False))
    else:
        chosen_neg = []
    days = sorted(set(events) | set(chosen_neg))
    return days


def _list_and_download(fs, d: date, hour: int, dest: Path) -> Path | None:
    primary = _bucket_for(d)
    buckets = [primary] + [b for b in FALLBACKS if b != primary]
    doy = int(d.strftime("%j"))
    keys = []
    used = None
    for bucket in buckets:
        prefix = f"{bucket}/{PRODUCT}/{d.year}/{doy:03d}/{hour:02d}/"
        try:
            keys = fs.ls(prefix)
        except Exception:
            keys = []
        if keys:
            used = bucket
            break
    if not keys:
        return None
    # arquivo do meio da hora
    key = sorted(keys)[len(keys) // 2]
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  ↓ s3://{key} → {dest.name}")
    fs.get(key, str(dest))
    return dest


def _latlon_to_goes_xy(lat: float, lon: float, lon_origin: float) -> tuple[float, float]:
    r_eq = 6378137.0
    r_pol = 6356752.31414
    h = 35786023.0
    lat_r = np.deg2rad(lat)
    lon_r = np.deg2rad(lon)
    lon_origin_r = np.deg2rad(lon_origin)
    phi_c = 0.0
    rc = r_pol / np.sqrt(1 - (r_eq**2 - r_pol**2) / r_eq**2 * np.cos(phi_c) ** 2)
    s_x = h - rc * np.cos(lat_r) * np.cos(lon_r - lon_origin_r)
    s_y = -rc * np.cos(lat_r) * np.sin(lon_r - lon_origin_r)
    s_z = rc * np.sin(lat_r)
    x = np.arctan2(s_y, s_x)
    y = np.arcsin(s_z / np.sqrt(s_x**2 + s_y**2 + s_z**2))
    return float(x), float(y)


def _band_map(ds) -> dict[str, str]:
    out = {}
    for v in ds.data_vars:
        m = _CMI_BAND_RE.search(str(v))
        if m:
            out[f"C{m.group(1)}"] = v
    return out


def _extract_day(
    files: list[tuple[int, Path]],
    km_coords: dict[int, tuple[float, float]],
    day: date,
) -> list[dict]:
    import xarray as xr

    band_cols: dict[int, dict[str, float]] = defaultdict(dict)
    hours_found: dict[int, int] = defaultdict(int)
    for hour, path in files:
        try:
            ds = xr.open_dataset(path)
        except Exception as exc:
            print(f"    open fail {path.name}: {exc}")
            continue
        try:
            lon_origin = float(ds["goes_imager_projection"].longitude_of_projection_origin)
            bmap = _band_map(ds)
            if not bmap:
                continue
            sample = next(iter(bmap.values()))
            da0 = ds[sample]
            if "y" not in da0.dims or "x" not in da0.dims:
                continue
            y_coords = da0["y"].values
            x_coords = da0["x"].values
            km_ix = {}
            for km, (lat, lon) in km_coords.items():
                x_t, y_t = _latlon_to_goes_xy(lat, lon, lon_origin)
                iy = int(np.abs(y_coords - y_t).argmin())
                ix = int(np.abs(x_coords - x_t).argmin())
                km_ix[km] = (iy, ix)
            arrays = {bid: ds[var].values for bid, var in bmap.items() if "y" in ds[var].dims}
            for km, (iy, ix) in km_ix.items():
                for bid, arr in arrays.items():
                    v = float(arr[iy, ix])
                    band_cols[km][f"{bid}_h{hour:02d}"] = v if np.isfinite(v) else np.nan
                hours_found[km] += 1
        finally:
            ds.close()
    rows = []
    for km, bands in band_cols.items():
        rec = {
            "day": pd.Timestamp(day),
            "km_efc": int(km),
            "goes_hours_found": int(hours_found[km]),
            "is_synth_goes": 0,
        }
        rec.update(bands)
        rows.append(rec)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-10-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--kms", default="", help="lista csv de km; default = cascade_v2")
    ap.add_argument(
        "--uniplu-catalog",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "uniplu_efc_catalog_r20km.csv",
    )
    ap.add_argument("--neg-ratio", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-days", type=int, default=0, help="limite p/ smoke (0=todos)")
    ap.add_argument("--upload-gcs", action="store_true")
    args = ap.parse_args()

    d0 = date.fromisoformat(args.start)
    d1 = date.fromisoformat(args.end)
    print(f"1) Coords km…")
    km_coords = _load_kms(args)
    print(f"   {len(km_coords)} km: {sorted(km_coords)[:8]}…")

    print(f"2) Dias UNIPLU {d0}→{d1}…")
    events, all_days = _uniplu_event_days(d0, d1, args.uniplu_catalog)
    days = _pick_days(events, all_days, args.neg_ratio, args.seed)
    if args.max_days and args.max_days > 0:
        days = days[: args.max_days]
    print(f"   eventos={len(events)} · selecionados={len(days)} (neg_ratio={args.neg_ratio})")

    import s3fs

    fs = s3fs.S3FileSystem(anon=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="goes_hist_"))
    rows: list[dict] = []
    ok_days = 0
    for i, d in enumerate(days, 1):
        print(f"[{i}/{len(days)}] {d.isoformat()}")
        day_dir = tmp_root / d.isoformat()
        files: list[tuple[int, Path]] = []
        for h in HOURS:
            dest = day_dir / f"mcmipc_{h:02d}.nc"
            try:
                p = _list_and_download(fs, d, h, dest)
            except Exception as exc:
                print(f"  download fail {h:02d}: {exc}")
                p = None
            if p and p.is_file():
                files.append((h, p))
        if files:
            try:
                rows.extend(_extract_day(files, km_coords, d))
                ok_days += 1
            except Exception as exc:
                print(f"  extract fail: {exc}")
        # limpa NetCDF do dia
        for _, p in files:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            if day_dir.exists():
                for p in day_dir.glob("*"):
                    p.unlink(missing_ok=True)
                day_dir.rmdir()
        except Exception:
            pass

    if not rows:
        print("Nenhuma feature extraída.")
        return 1
    out = pd.DataFrame(rows)
    out["day"] = pd.to_datetime(out["day"]).dt.normalize()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"OK → {args.out} · {len(out)} linhas · {out['day'].nunique()} dias · ok_days={ok_days}")

    if args.upload_gcs:
        from google.cloud import storage

        blob = f"ml/raijin-trainkit/data/ml/cascade_v2/features_goes_hist_{d0}_{d1}.csv"
        storage.Client(project=PROJECT).bucket(BUCKET_GCS).blob(blob).upload_from_filename(str(args.out))
        print(f"GCS → gs://{BUCKET_GCS}/{blob}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
