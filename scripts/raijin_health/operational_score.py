#!/usr/bin/env python3
"""Score operacional Raijin (cascade_v2) → CSV → BigQuery ``raijin_alerts``.

Pipeline mínimo para religar o path ML:
  1. Lê GOES-19 MCMIPC já staged em ``gs://efc-clima-data/ml/raijin-trainkit/data/goes_abi/``
  2. Amostra bandas nos km da ferrovia (locations BQ)
  3. Junta ``bq_ecmwf_precip_24h_mm`` + MERGE/EWS (label / gate)
  4. SVM cascade_v2 + gate precip (≥ wn2_precip_min_mm) + fallback GOES-only
  5. Carrega em ``efc_clima.raijin_alerts``

Uso:
  python3 scripts/raijin_health/operational_score.py \\
    --day-start 2026-11-17 --day-end 2026-11-19 --load-bq
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = os.environ.get("GCP_PROJECT", "climaticsystem")
DATASET = os.environ.get("BQ_DATASET", "efc_clima")
BUCKET = os.environ.get("GCS_BUCKET", "efc-clima-data")
TRAINKIT_PREFIX = os.environ.get("RAIJIN_TRAINKIT_PREFIX", "ml/raijin-trainkit")
MODEL_BLOB = f"{TRAINKIT_PREFIX}/data/ml/cascade_v2/cascade_v2_model.joblib"
GOES_PREFIX = f"{TRAINKIT_PREFIX}/data/goes_abi/"
GOES_HOURS = (0, 6, 12, 18)
THRESHOLD_MM = 49.0
_CMI_BAND_RE = re.compile(r"CMI_C(\d{2})", re.I)


def _bq():
    from google.cloud import bigquery

    return bigquery.Client(project=PROJECT)


def _gcs():
    from google.cloud import storage

    return storage.Client(project=PROJECT)


def _download_model(local: Path) -> Path:
    bucket = _gcs().bucket(BUCKET)
    blob = bucket.blob(MODEL_BLOB)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{BUCKET}/{MODEL_BLOB}")
    local.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local))
    return local


def _list_goes_blobs(day_start: date, day_end: date) -> list[tuple[date, int, str]]:
    """Retorna (day, hour_utc, blob_name) a partir do staging GCS."""
    bucket = _gcs().bucket(BUCKET)
    out: list[tuple[date, int, str]] = []
    d = day_start
    while d <= day_end:
        prefix = f"{GOES_PREFIX}{d.isoformat()}/"
        for blob in bucket.list_blobs(prefix=prefix):
            name = blob.name.rsplit("/", 1)[-1]
            # OR_ABI-L2-MCMIPC-..._sYYYYDOYHH...
            m = re.search(r"_s(\d{7})(\d{2})", name)
            hour = int(m.group(2)) if m else None
            if hour is None:
                continue
            # snap to nearest configured hour
            nearest = min(GOES_HOURS, key=lambda h: abs(h - hour))
            if abs(nearest - hour) > 1:
                continue
            out.append((d, nearest, blob.name))
        d += timedelta(days=1)
    # one file per day×hour
    best: dict[tuple[date, int], str] = {}
    for day, hour, blob_name in out:
        best[(day, hour)] = blob_name
    return [(d, h, b) for (d, h), b in sorted(best.items())]


def _load_km_coords() -> dict[int, tuple[float, float]]:
    sql = f"""
    SELECT CAST(ROUND(km_efc) AS INT64) AS km_efc, lat, lon
    FROM `{PROJECT}.{DATASET}.locations`
    WHERE source = 'railway_km' AND km_efc IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL
    """
    df = _bq().query(sql).to_dataframe(create_bqstorage_client=False)
    return {
        int(r.km_efc): (float(r.lat), float(r.lon))
        for r in df.itertuples(index=False)
    }


def _load_ecmwf_precip(day_start: date, day_end: date) -> pd.DataFrame:
    sql = f"""
    SELECT s.date AS day,
           CAST(ROUND(l.km_efc) AS INT64) AS km_efc,
           MAX(s.value) AS bq_ecmwf_precip_24h_mm
    FROM `{PROJECT}.{DATASET}.clima_series` s
    JOIN `{PROJECT}.{DATASET}.locations` l ON l.location_id = s.location_id
    WHERE s.source = 'ecmwf'
      AND s.variable = 'precip_24h_mm'
      AND l.source = 'railway_km'
      AND s.date BETWEEN @d0 AND @d1
    GROUP BY day, km_efc
    """
    from google.cloud import bigquery

    job = _bq().query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d0", "DATE", day_start.isoformat()),
                bigquery.ScalarQueryParameter("d1", "DATE", day_end.isoformat()),
            ]
        ),
    )
    df = job.to_dataframe(create_bqstorage_client=False)
    if df.empty:
        return df
    df["day"] = pd.to_datetime(df["day"]).dt.normalize()
    return df


def _load_merge_ews(day_start: date, day_end: date) -> pd.DataFrame:
    """Labels + gate precip a partir de MERGE 6h e Orion/EWS."""
    from google.cloud import bigquery

    sql = f"""
    WITH loc AS (
      SELECT location_id, CAST(ROUND(km_efc) AS INT64) AS km_efc
      FROM `{PROJECT}.{DATASET}.locations`
      WHERE source = 'railway_km' AND km_efc IS NOT NULL
    ),
    merge_d AS (
      SELECT s.date AS day, l.km_efc, SUM(s.value) AS merge_mm
      FROM `{PROJECT}.{DATASET}.clima_series` s
      JOIN loc l ON l.location_id = s.location_id
      WHERE s.source = 'merge'
        AND s.variable LIKE 'precip_mm_6h_%'
        AND s.date BETWEEN @d0 AND @d1
      GROUP BY day, km_efc
    ),
    ews_d AS (
      SELECT s.date AS day,
             CAST(ROUND(l.km_efc) AS INT64) AS km_efc,
             SUM(s.value) AS ews_mm
      FROM `{PROJECT}.{DATASET}.clima_series` s
      JOIN `{PROJECT}.{DATASET}.locations` l ON l.location_id = s.location_id
      WHERE s.source = 'orion'
        AND s.variable IN ('precip_mm', 'precip_24h_mm')
        AND l.km_efc IS NOT NULL
        AND s.date BETWEEN @d0 AND @d1
      GROUP BY day, km_efc
    )
    SELECT COALESCE(m.day, e.day) AS day,
           COALESCE(m.km_efc, e.km_efc) AS km_efc,
           m.merge_mm,
           e.ews_mm
    FROM merge_d m
    FULL OUTER JOIN ews_d e USING (day, km_efc)
    """
    job = _bq().query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d0", "DATE", day_start.isoformat()),
                bigquery.ScalarQueryParameter("d1", "DATE", day_end.isoformat()),
            ]
        ),
    )
    df = job.to_dataframe(create_bqstorage_client=False)
    if df.empty:
        return df
    df["day"] = pd.to_datetime(df["day"]).dt.normalize()
    df["merge_mm"] = pd.to_numeric(df["merge_mm"], errors="coerce")
    df["ews_mm"] = pd.to_numeric(df["ews_mm"], errors="coerce")
    df["label"] = (
        (df["merge_mm"].fillna(0) >= THRESHOLD_MM)
        | (df["ews_mm"].fillna(0) >= THRESHOLD_MM)
    ).astype(int)
    return df


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


def _band_var_names(ds) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for v in ds.data_vars:
        m = _CMI_BAND_RE.search(str(v))
        if m:
            mapping[f"C{m.group(1)}"] = v
    return mapping


def _extract_goes_features(
    goes_files: list[tuple[date, int, Path]],
    km_coords: dict[int, tuple[float, float]],
) -> pd.DataFrame:
    import xarray as xr

    band_cols: dict[tuple, dict[str, float]] = defaultdict(dict)
    hours_found: dict[tuple, int] = defaultdict(int)

    for day, hour, path in goes_files:
        print(f"  GOES {day} {hour:02d}UTC ← {path.name}")
        try:
            ds = xr.open_dataset(path)
        except Exception as exc:
            print(f"    skip open: {exc}")
            continue
        try:
            proj = ds["goes_imager_projection"]
            lon_origin = float(proj.longitude_of_projection_origin)
            band_map = _band_var_names(ds)
            if not band_map:
                print("    sem bandas CMI")
                continue
            # Index once per file: map each km → nearest (y,x) indices
            sample_var = next(iter(band_map.values()))
            da0 = ds[sample_var]
            if "y" not in da0.dims or "x" not in da0.dims:
                continue
            y_coords = da0["y"].values
            x_coords = da0["x"].values
            km_ix: dict[int, tuple[int, int]] = {}
            for km, (lat, lon) in km_coords.items():
                x_t, y_t = _latlon_to_goes_xy(lat, lon, lon_origin)
                iy = int(np.abs(y_coords - y_t).argmin())
                ix = int(np.abs(x_coords - x_t).argmin())
                km_ix[km] = (iy, ix)
            band_arrays = {
                bid: ds[var].values for bid, var in band_map.items() if "y" in ds[var].dims
            }
            for km, (iy, ix) in km_ix.items():
                key = (pd.Timestamp(day), int(km))
                got = False
                for band_id, arr in band_arrays.items():
                    v = float(arr[iy, ix])
                    if not np.isfinite(v):
                        v = np.nan
                    band_cols[key][f"{band_id}_h{hour:02d}"] = v
                    got = True
                if got:
                    hours_found[key] += 1
        finally:
            ds.close()

    rows = []
    for (day, km), bands in band_cols.items():
        rec = {
            "day": pd.Timestamp(day).normalize(),
            "km_efc": int(km),
            "goes_hours_found": hours_found[(day, km)],
        }
        rec.update(bands)
        rows.append(rec)
    df = pd.DataFrame(rows)
    print(f"  Features GOES: {len(df)} linhas day×km")
    return df


def _score(df: pd.DataFrame, model_path: Path) -> pd.DataFrame:
    import joblib

    pack = joblib.load(model_path)
    feat_cols = list(pack["features"])
    t_svm = float(pack["svm_threshold"])
    t_go = float(pack.get("svm_threshold_goes_only", t_svm))
    t_gate = float(pack.get("wn2_precip_min_mm", 10.0))
    scaler = pack["scaler"]
    model = pack["model"]

    work = df.copy()
    for c in feat_cols:
        if c not in work.columns:
            work[c] = np.nan
        work[c] = pd.to_numeric(work[c], errors="coerce")
    # fill missing bands with column median (fallback 0)
    for c in feat_cols:
        med = work[c].median(skipna=True)
        work[c] = work[c].fillna(0.0 if pd.isna(med) else float(med))

    X = scaler.transform(work[feat_cols].values.astype(float))
    work["svm_score"] = model.decision_function(X)

    # Gate operacional: MERGE obs primeiro; ECMWF como fallback (WN2 ausente).
    merge = pd.to_numeric(work.get("merge_mm"), errors="coerce") if "merge_mm" in work.columns else pd.Series(np.nan, index=work.index)
    ecmwf = pd.to_numeric(work.get("bq_ecmwf_precip_24h_mm"), errors="coerce") if "bq_ecmwf_precip_24h_mm" in work.columns else pd.Series(np.nan, index=work.index)
    precip = merge.fillna(ecmwf).fillna(0.0)
    has_gate = ((merge.fillna(0) > 0) | (ecmwf.fillna(0) > 0)).astype(int)

    cascade = (
        (work["svm_score"].values >= t_svm)
        & (precip.values >= t_gate)
        & (has_gate.values == 1)
    )
    # GOES-only fallback OFF por padrão (t_goes≈t_svm dispara quase tudo).
    goes_fallback = os.environ.get("RAIJIN_GOES_FALLBACK", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if goes_fallback:
        goes_only = work["svm_score"].values >= t_go
        work["pred_cascade"] = np.where(cascade, 1, goes_only).astype(int)
    else:
        work["pred_cascade"] = cascade.astype(int)
    work["gate_precip_mm"] = precip
    work["t_svm"] = t_svm
    work["t_gate"] = t_gate
    return work


def _load_bq(pred: pd.DataFrame, *, model_id: str) -> int:
    from google.cloud import bigquery

    client = _bq()
    table = f"{PROJECT}.{DATASET}.raijin_alerts"
    client.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{table}` (
          day DATE NOT NULL,
          km_efc INT64 NOT NULL,
          pred INT64 NOT NULL,
          label INT64,
          outcome STRING,
          model_id STRING,
          svm_score FLOAT64,
          ews_mm FLOAT64,
          ingested_at TIMESTAMP
        )
        """
    ).result()

    days = sorted(pd.to_datetime(pred["day"]).dt.date.unique())
    if days:
        client.query(
            f"DELETE FROM `{table}` WHERE day BETWEEN @d0 AND @d1",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("d0", "DATE", str(days[0])),
                    bigquery.ScalarQueryParameter("d1", "DATE", str(days[-1])),
                ]
            ),
        ).result()

    now = datetime.now(timezone.utc)
    rows = []
    for r in pred.itertuples(index=False):
        day = pd.Timestamp(r.day).date()
        label = int(r.label) if hasattr(r, "label") and pd.notna(r.label) else None
        pred_i = int(r.pred_cascade)
        if label is None:
            outcome = "pending"
        elif pred_i == 1 and label == 1:
            outcome = "tp"
        elif pred_i == 1 and label == 0:
            outcome = "fp"
        elif pred_i == 0 and label == 1:
            outcome = "fn"
        else:
            outcome = "tn"
        rows.append(
            {
                "day": day.isoformat(),
                "km_efc": int(r.km_efc),
                "pred": pred_i,
                "label": label,
                "outcome": outcome,
                "model_id": model_id,
                "svm_score": float(r.svm_score) if pd.notna(r.svm_score) else None,
                "ews_mm": float(r.ews_mm) if hasattr(r, "ews_mm") and pd.notna(r.ews_mm) else None,
                "ingested_at": now.isoformat(),
            }
        )

    # Match existing table schema (day/km_efc/pred are REQUIRED).
    df_out = pd.DataFrame(rows)
    df_out["day"] = pd.to_datetime(df_out["day"]).dt.date
    df_out["km_efc"] = df_out["km_efc"].astype(int)
    df_out["pred"] = df_out["pred"].astype(int)
    df_out["ingested_at"] = pd.to_datetime(df_out["ingested_at"], utc=True)
    job = client.load_table_from_dataframe(
        df_out,
        table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ),
    )
    job.result()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day-start", default="2026-11-17")
    ap.add_argument("--day-end", default="2026-11-19")
    ap.add_argument("--out-csv", type=Path, default=Path("/tmp/raijin_preds_ops.csv"))
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--load-bq", action="store_true")
    ap.add_argument("--model-id", default="cascade_v2_ops")
    ap.add_argument("--max-kms", type=int, default=0, help="0=all; useful for smoke")
    args = ap.parse_args()

    d0 = date.fromisoformat(args.day_start)
    d1 = date.fromisoformat(args.day_end)
    work = Path(args.work_dir or tempfile.mkdtemp(prefix="raijin_ops_"))
    goes_dir = work / "goes"
    goes_dir.mkdir(parents=True, exist_ok=True)

    print(f"Work dir: {work}")
    print("1) Listando GOES staged…")
    goes_blobs = _list_goes_blobs(d0, d1)
    if not goes_blobs:
        print("ERRO: nenhum GOES ABI no GCS para o intervalo", file=sys.stderr)
        return 1
    print(f"   {len(goes_blobs)} arquivos day×hour")

    bucket = _gcs().bucket(BUCKET)
    local_goes: list[tuple[date, int, Path]] = []
    for day, hour, blob_name in goes_blobs:
        dest = goes_dir / f"{day.isoformat()}_h{hour:02d}.nc"
        if not dest.exists():
            print(f"   download {blob_name}")
            bucket.blob(blob_name).download_to_filename(str(dest))
        local_goes.append((day, hour, dest))

    print("2) Coordenadas km…")
    km_coords = _load_km_coords()
    if args.max_kms and args.max_kms > 0:
        # keep evenly spaced subset for smoke
        keys = sorted(km_coords)
        step = max(1, len(keys) // args.max_kms)
        keep = set(keys[::step][: args.max_kms])
        km_coords = {k: v for k, v in km_coords.items() if k in keep}
    print(f"   {len(km_coords)} km")

    print("3) Extraindo features GOES…")
    goes_df = _extract_goes_features(local_goes, km_coords)
    if goes_df.empty:
        print("ERRO: features GOES vazias", file=sys.stderr)
        return 1

    print("4) ECMWF + MERGE/EWS…")
    ecmwf = _load_ecmwf_precip(d0, d1)
    labels = _load_merge_ews(d0, d1)
    feat = goes_df.merge(ecmwf, on=["day", "km_efc"], how="left")
    feat = feat.merge(labels, on=["day", "km_efc"], how="left")
    feat["label"] = feat["label"].fillna(0).astype(int)

    print("5) Scoring cascade_v2…")
    model_path = work / "cascade_v2_model.joblib"
    _download_model(model_path)
    scored = _score(feat, model_path)
    n_alert = int((scored["pred_cascade"] == 1).sum())
    print(
        f"   {len(scored)} linhas · {scored['day'].nunique()} dias · "
        f"{n_alert} alertas (pred=1)"
    )

    out_cols = [
        "day",
        "km_efc",
        "pred_cascade",
        "svm_score",
        "label",
        "ews_mm",
        "merge_mm",
        "bq_ecmwf_precip_24h_mm",
        "gate_precip_mm",
        "goes_hours_found",
    ]
    scored[out_cols].to_csv(args.out_csv, index=False)
    print(f"CSV → {args.out_csv}")

    if args.load_bq:
        print("6) Load BQ raijin_alerts…")
        n = _load_bq(scored, model_id=args.model_id)
        print(f"   {n} linhas em {DATASET}.raijin_alerts")

    summary = {
        "day_start": args.day_start,
        "day_end": args.day_end,
        "n_rows": int(len(scored)),
        "n_alert": n_alert,
        "n_tp": int(((scored["pred_cascade"] == 1) & (scored["label"] == 1)).sum()),
        "n_fp": int(((scored["pred_cascade"] == 1) & (scored["label"] == 0)).sum()),
        "n_fn": int(((scored["pred_cascade"] == 0) & (scored["label"] == 1)).sum()),
        "csv": str(args.out_csv),
        "model_id": args.model_id,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
