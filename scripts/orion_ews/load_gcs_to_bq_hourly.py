#!/usr/bin/env python3
"""Cheap Orion → BQ load from existing GCS NDJSON (no Orion API call).

- Buckets 15‑min tips into **hourly** precip (sum) with ``date`` + ``valid_time``
  matching BNDMET ``estacoes`` shape (no daily-only aggregation).
- Upserts ``locations`` (source=orion) and refreshes ``orion_devices``.
- Idempotent: DELETE overlapping Orion precip window before APPEND.

Usage:
  python3 -m scripts.orion_ews.load_gcs_to_bq_hourly \\
    --ndjson gs://efc-clima-data/obs/ews/orion_clima_series_latest.ndjson \\
    --locations gs://efc-clima-data/obs/ews/orion_locations_latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery, storage


def _read_ndjson(path_or_gs: str) -> list[dict[str, Any]]:
    if path_or_gs.startswith("gs://"):
        _, _, rest = path_or_gs.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        client = storage.Client()
        raw = client.bucket(bucket_name).blob(blob_name).download_as_text()
    else:
        raw = Path(path_or_gs).read_text(encoding="utf-8")
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _read_json(path_or_gs: str) -> Any:
    if path_or_gs.startswith("gs://"):
        _, _, rest = path_or_gs.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        client = storage.Client()
        raw = client.bucket(bucket_name).blob(blob_name).download_as_text()
    else:
        raw = Path(path_or_gs).read_text(encoding="utf-8")
    return json.loads(raw)


def to_hourly_precip(rows: list[dict[str, Any]], *, source: str = "orion") -> list[dict[str, Any]]:
    """Sum precip_mm into UTC hour buckets; keep date + valid_time (hourly)."""
    buckets: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        if (r.get("variable") or "") != "precip_mm":
            continue
        loc = r.get("location_id")
        raw_dt = r.get("date") or r.get("valid_time") or r.get("obs_time")
        if not loc or not raw_dt:
            continue
        try:
            ts = datetime.fromisoformat(str(raw_dt).replace("Z", "")[:19])
        except ValueError:
            continue
        hour = ts.replace(minute=0, second=0, microsecond=0)
        try:
            val = float(r.get("value"))
        except (TypeError, ValueError):
            continue
        key = (str(loc), hour.isoformat(sep="T"))
        buckets[key] += val

    now = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    for (loc, hour_iso), mm in sorted(buckets.items()):
        hour = datetime.fromisoformat(hour_iso)
        out.append(
            {
                "date": hour.date().isoformat(),  # DATE
                "location_id": loc,
                "variable": "precip_mm",
                "value": float(mm),
                "source": source,
                "ingested_at": now,
                "valid_time": hour.isoformat(sep=" "),  # TIMESTAMP
            }
        )
    return out


def load_clima(client: bigquery.Client, rows: list[dict[str, Any]], *, project: str, dataset: str) -> int:
    if not rows:
        print("[bq] no hourly rows")
        return 0
    table = f"{project}.{dataset}.clima_series"
    dates = sorted({r["date"] for r in rows})
    d0, d1 = dates[0], dates[-1]
    source = rows[0]["source"]
    del_sql = f"""
    DELETE FROM `{table}`
    WHERE source = @source
      AND variable = 'precip_mm'
      AND date BETWEEN @d0 AND @d1
    """
    client.query(
        del_sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("d0", "DATE", d0),
                bigquery.ScalarQueryParameter("d1", "DATE", d1),
            ]
        ),
    ).result()
    print(f"[bq] deleted prior {source}/precip_mm {d0}→{d1}")

    # Load via local temp NDJSON (load job = cheap vs streaming)
    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False) as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp = fh.name
    job = client.load_table_from_file(
        open(tmp, "rb"),
        table,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            ignore_unknown_values=True,
        ),
    )
    job.result()
    print(f"[bq] appended {job.output_rows} hourly precip rows → {table}")
    return int(job.output_rows or 0)


def upsert_locations(client: bigquery.Client, locations: list[dict[str, Any]], *, project: str, dataset: str) -> int:
    """MERGE into locations (source=orion). extra is INT in live schema — skip name there."""
    if not locations:
        return 0
    table = f"{project}.{dataset}.locations"
    # staging
    stage = f"{project}.{dataset}._orion_locations_stage"
    client.query(f"DROP TABLE IF EXISTS `{stage}`").result()
    rows = []
    for loc in locations:
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            continue
        # Live locations.extra is INT64 — never send names/strings here.
        rows.append(
            {
                "location_id": loc["location_id"],
                "km_efc": float(loc["km_efc"]) if loc.get("km_efc") is not None else None,
                "lat": float(lat),
                "lon": float(lon),
                "source": "orion",
            }
        )
    if not rows:
        return 0
    schema = [
        bigquery.SchemaField("location_id", "STRING"),
        bigquery.SchemaField("km_efc", "FLOAT"),
        bigquery.SchemaField("lat", "FLOAT"),
        bigquery.SchemaField("lon", "FLOAT"),
        bigquery.SchemaField("source", "STRING"),
    ]
    job = client.load_table_from_json(
        rows,
        stage,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        ),
    )
    job.result()
    merge = f"""
    MERGE `{table}` T
    USING `{stage}` S
    ON T.location_id = S.location_id
    WHEN MATCHED THEN UPDATE SET
      km_efc = S.km_efc, lat = S.lat, lon = S.lon, source = S.source
    WHEN NOT MATCHED THEN INSERT (location_id, km_efc, lat, lon, source, extra, indice_suscetibilidade)
    VALUES (S.location_id, S.km_efc, S.lat, S.lon, S.source, CAST(NULL AS INT64), CAST(NULL AS INT64))
    """
    client.query(merge).result()
    client.query(f"DROP TABLE IF EXISTS `{stage}`").result()
    print(f"[bq] locations MERGE ok ({len(rows)} orion stations)")
    return len(rows)


def upsert_orion_devices(client: bigquery.Client, locations: list[dict[str, Any]], *, project: str, dataset: str) -> None:
    table = f"{project}.{dataset}.orion_devices"
    client.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{table}` (
          source STRING, location_id STRING, station_id STRING, name STRING,
          km_efc FLOAT64, lat FLOAT64, lon FLOAT64, status STRING,
          battery_pct INT64, serial_number STRING, last_upload STRING,
          orion_device_id INT64, pulled_at TIMESTAMP
        )
        """
    ).result()
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for loc in locations:
        extra = loc.get("extra") or {}
        rows.append(
            {
                "source": "orion",
                "location_id": loc.get("location_id"),
                "station_id": loc.get("station_id"),
                "name": loc.get("name"),
                "km_efc": loc.get("km_efc"),
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "status": extra.get("status"),
                "battery_pct": extra.get("battery_pct"),
                "serial_number": extra.get("serial_number"),
                "last_upload": extra.get("last_upload"),
                "orion_device_id": extra.get("orion_device_id"),
                "pulled_at": now,
            }
        )
    if not rows:
        return
    client.query(f"DELETE FROM `{table}` WHERE source='orion'").result()
    job = client.load_table_from_json(
        rows,
        table,
        job_config=bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND),
    )
    job.result()
    print(f"[bq] orion_devices refreshed ({len(rows)})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ndjson", default="gs://efc-clima-data/obs/ews/orion_clima_series_latest.ndjson")
    ap.add_argument("--locations", default="gs://efc-clima-data/obs/ews/orion_locations_latest.json")
    ap.add_argument("--project", default="climaticsystem")
    ap.add_argument("--dataset", default="efc_clima")
    ap.add_argument("--source", default="orion")
    args = ap.parse_args()

    raw = _read_ndjson(args.ndjson)
    hourly = to_hourly_precip(raw, source=args.source)
    print(f"[xform] {len(raw)} raw → {len(hourly)} hourly precip rows")

    locs_raw = _read_json(args.locations)
    locations = locs_raw if isinstance(locs_raw, list) else locs_raw.get("locations") or []

    client = bigquery.Client(project=args.project)
    n = load_clima(client, hourly, project=args.project, dataset=args.dataset)
    upsert_locations(client, locations, project=args.project, dataset=args.dataset)
    upsert_orion_devices(client, locations, project=args.project, dataset=args.dataset)
    print(f"[done] hourly rows loaded={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
