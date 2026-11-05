#!/usr/bin/env python3
"""Pull Orion (EWS) devices + readings → local JSON/NDJSON (and optional BQ/GCS hooks).

Smoke-test (requires key in env, never argv):
  export ORION_USER_KEY='…'   # cole só a User Key; não a senha
  python3 -m scripts.orion_ews.ingest catalog --out /tmp/orion_devices.json
  python3 -m scripts.orion_ews.ingest readings --days 7 --out /tmp/orion_readings.ndjson

Dry-run without calling Orion:
  python3 -m scripts.orion_ews.ingest catalog --fixture scripts/orion_ews/fixtures/user_devices_sample.json

BQ load is intentionally gated: pass --bq-project climaticsystem only when
ADC / workload identity is available. Secrets stay in Secret Manager / env.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Allow `python3 scripts/orion_ews/ingest.py` and `python3 -m scripts.orion_ews.ingest`
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.orion_ews.client import (  # noqa: E402
    OrionAuthError,
    OrionClient,
    flatten_device_sensors,
    redact_secret,
)
from scripts.orion_ews.mapping import (  # noqa: E402
    SOURCE_ORION,
    device_to_location_row,
    map_sensor_type,
    reading_to_clima_row,
    station_id_from_device,
)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    # Stable column union
    fieldnames: list[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in r.items()}
            w.writerow(flat)


def cmd_catalog(args: argparse.Namespace) -> int:
    if args.fixture:
        devices = _load_json(Path(args.fixture))
        print(f"[fixture] {len(devices)} devices from {args.fixture}")
    else:
        client = OrionClient.from_env()
        print(f"[auth] ORION_USER_KEY={redact_secret(client.api_key)} → /token …", flush=True)
        client.refresh_token()
        print("[auth] bearer ok (não exibido)", flush=True)
        serials = [s.strip() for s in (args.serial or "").split(",") if s.strip()] or None
        devices = client.list_devices(serial_numbers=serials)
        print(f"[catalog] {len(devices)} devices", flush=True)

    sensors = flatten_device_sensors(devices)
    locations = [device_to_location_row(d) for d in devices]
    summary = []
    for d in devices:
        summary.append(
            {
                "station_id": station_id_from_device(d),
                "device_name": d.get("deviceName"),
                "serial_number": d.get("serialNumber"),
                "status": d.get("status"),
                "battery_pct": d.get("batteryPercentage"),
                "lat": d.get("latitude"),
                "lon": d.get("longitude"),
                "last_upload": d.get("lastUpload"),
                "n_raw_sensors": len(d.get("sensors") or []),
                "n_soft_sensors": len(d.get("softSensors") or []),
                "mapped_variables": sorted(
                    {
                        v
                        for s in (d.get("sensors") or []) + (d.get("softSensors") or [])
                        if (v := map_sensor_type(s.get("sensorType"), s.get("uom")))
                    }
                ),
            }
        )

    out = Path(args.out)
    if out.suffix.lower() == ".csv":
        _write_csv(out, summary)
    else:
        _write_json(
            out,
            {
                "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": SOURCE_ORION,
                "devices": devices,
                "sensors_flat": sensors,
                "locations": locations,
                "summary": summary,
            },
        )
    sensors_out = Path(args.sensors_out) if args.sensors_out else out.with_name(out.stem + "_sensors.csv")
    _write_csv(sensors_out, sensors)
    print(f"wrote {out} and {sensors_out}")
    for s in summary:
        print(
            f"  - {s['station_id']}: {s['device_name']} "
            f"[{s['status']}] bat={s['battery_pct']}% "
            f"vars={s['mapped_variables']}"
        )
    return 0


def _date_range(args: argparse.Namespace) -> tuple[str, str]:
    if args.start and args.end:
        return args.start, args.end
    end = date.today()
    start = end - timedelta(days=int(args.days))
    return start.isoformat(), end.isoformat()


def cmd_readings(args: argparse.Namespace) -> int:
    start, end = _date_range(args)
    if args.fixture_devices:
        devices = _load_json(Path(args.fixture_devices))
        if isinstance(devices, dict) and "devices" in devices:
            devices = devices["devices"]
    else:
        client = OrionClient.from_env()
        client.refresh_token()
        serials = [s.strip() for s in (args.serial or "").split(",") if s.strip()] or None
        devices = client.list_devices(serial_numbers=serials)

    by_device_id = {d.get("deviceId"): d for d in devices}
    flat = flatten_device_sensors(devices)

    # Prefer soft sensors when both exist for same type on a device.
    soft_keys = {
        (r["device_id"], (r.get("sensor_type") or "").lower())
        for r in flat
        if r["kind"] == "soft"
    }

    selected: list[dict[str, Any]] = []
    for r in flat:
        if args.raw_only and r["kind"] != "raw":
            continue
        if args.soft_only and r["kind"] != "soft":
            continue
        if (
            not args.raw_only
            and r["kind"] == "raw"
            and (r["device_id"], (r.get("sensor_type") or "").lower()) in soft_keys
        ):
            continue  # skip raw when soft twin exists
        selected.append(r)

    raw_ids = sorted({int(r["sensor_id"]) for r in selected if r.get("sensor_id") is not None})
    soft_ids = sorted(
        {int(r["soft_sensor_id"]) for r in selected if r.get("soft_sensor_id") is not None}
    )
    print(
        f"[readings] devices={len(devices)} sensors_selected={len(selected)} "
        f"raw_ids={len(raw_ids)} soft_ids={len(soft_ids)} window={start}→{end}",
        flush=True,
    )

    if args.fixture_readings:
        readings = _load_json(Path(args.fixture_readings))
    else:
        if args.dry_run:
            print("[dry-run] skipping Orion /sensorData call")
            readings = []
        else:
            client = OrionClient.from_env()
            client.refresh_token()
            # Chunk IDs to keep URLs short / stay under rate limits (60 req/min free).
            readings = []
            chunk = 25

            def chunks(xs: list[int], n: int) -> list[list[int]]:
                if not xs:
                    return []
                return [xs[i : i + n] for i in range(0, len(xs), n)]

            # Prefer a single call when small; otherwise page raw then soft.
            if len(raw_ids) + len(soft_ids) <= chunk:
                readings = client.fetch_sensor_data(
                    start_date=start,
                    end_date=end,
                    sensor_ids=raw_ids or None,
                    soft_sensor_ids=soft_ids or None,
                    validated=args.validated,
                )
                print(f"  fetched {len(readings)}", flush=True)
            else:
                for sid_chunk in chunks(raw_ids, chunk):
                    part = client.fetch_sensor_data(
                        start_date=start,
                        end_date=end,
                        sensor_ids=sid_chunk,
                        soft_sensor_ids=None,
                        validated=args.validated,
                    )
                    readings.extend(part)
                    print(f"  raw +{len(part)} (total {len(readings)})", flush=True)
                for ssid_chunk in chunks(soft_ids, chunk):
                    part = client.fetch_sensor_data(
                        start_date=start,
                        end_date=end,
                        sensor_ids=None,
                        soft_sensor_ids=ssid_chunk,
                        validated=args.validated,
                    )
                    readings.extend(part)
                    print(f"  soft +{len(part)} (total {len(readings)})", flush=True)

    # Index sensor meta
    meta_by_raw = {int(r["sensor_id"]): r for r in flat if r.get("sensor_id") is not None}
    meta_by_soft = {
        int(r["soft_sensor_id"]): r for r in flat if r.get("soft_sensor_id") is not None
    }

    staging: list[dict[str, Any]] = []
    clima: list[dict[str, Any]] = []
    for reading in readings:
        soft_id = reading.get("softSensorId")
        raw_id = reading.get("sensorId")
        meta = None
        if soft_id is not None and int(soft_id) in meta_by_soft:
            meta = meta_by_soft[int(soft_id)]
        elif raw_id is not None and int(raw_id) in meta_by_raw:
            meta = meta_by_raw[int(raw_id)]
        if meta is None:
            meta = {
                "kind": "soft" if soft_id else "raw",
                "sensor_id": raw_id,
                "soft_sensor_id": soft_id,
                "sensor_type": None,
                "uom": None,
            }
        device = by_device_id.get(meta.get("device_id"), {})
        staging.append(
            {
                "device_id": meta.get("device_id"),
                "station_id": station_id_from_device(device or meta),
                "sensor_id": raw_id,
                "soft_sensor_id": soft_id,
                "sensor_type": meta.get("sensor_type"),
                "uom": meta.get("uom"),
                "kind": meta.get("kind"),
                "reading_date": reading.get("readingDate"),
                "sensor_value": reading.get("sensorValue"),
                "quality_approval": reading.get("dataQualityApprovalLevelNameLong"),
                "quality_code": reading.get("dataQualityCodeLevelNameLong"),
                "comment": reading.get("comment"),
            }
        )
        row = reading_to_clima_row(device=device or meta, sensor_meta=meta, reading=reading)
        if row:
            clima.append(row)

    out = Path(args.out)
    if out.suffix.lower() == ".csv":
        _write_csv(out, staging)
    else:
        _write_ndjson(out, staging)
    clima_out = Path(args.clima_out) if args.clima_out else out.with_name(out.stem + "_clima_series.ndjson")
    _write_ndjson(clima_out, clima)
    print(f"wrote staging={out} ({len(staging)} rows) clima={clima_out} ({len(clima)} rows)")

    if args.bq_project:
        _maybe_load_bq(args, clima, locations=[device_to_location_row(d) for d in devices])
    return 0


def _maybe_load_bq(
    args: argparse.Namespace,
    clima_rows: list[dict[str, Any]],
    locations: list[dict[str, Any]],
) -> None:
    """Optional BigQuery load — requires google-cloud-bigquery + ADC."""
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        print(
            "[bq] google-cloud-bigquery não instalado; "
            "pip install google-cloud-bigquery  (pulando load)",
            file=sys.stderr,
        )
        return

    project = args.bq_project
    dataset = args.bq_dataset
    client = bigquery.Client(project=project)
    staging_table = f"{project}.{dataset}.orion_readings_staging"
    series_table = f"{project}.{dataset}.clima_series"
    loc_table = f"{project}.{dataset}.orion_devices"

    print(f"[bq] loading {len(clima_rows)} clima rows → {series_table} (append)")
    if not clima_rows:
        return
    # Narrow columns for clima_series long format
    series_payload = [
        {
            "source": r["source"],
            "station_id": r["station_id"],
            "variable": r["variable"],
            "value": r["value"],
            "obs_time": r["obs_time"],
        }
        for r in clima_rows
    ]
    job = client.load_table_from_json(
        series_payload,
        series_table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        ),
    )
    job.result()
    print(f"[bq] clima_series append ok ({job.output_rows} rows)")

    # Upsert-ish: replace orion_devices snapshot
    loc_payload = []
    for loc in locations:
        loc_payload.append(
            {
                "source": loc["source"],
                "station_id": loc["station_id"],
                "name": loc["name"],
                "lat": loc["lat"],
                "lon": loc["lon"],
                "status": (loc.get("extra") or {}).get("status"),
                "battery_pct": (loc.get("extra") or {}).get("battery_pct"),
                "serial_number": (loc.get("extra") or {}).get("serial_number"),
                "last_upload": (loc.get("extra") or {}).get("last_upload"),
                "orion_device_id": (loc.get("extra") or {}).get("orion_device_id"),
                "pulled_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    job2 = client.load_table_from_json(
        loc_payload,
        f"{project}.{dataset}.orion_devices",
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job2.result()
    print(f"[bq] orion_devices replaced ({job2.output_rows} rows); staging ref={staging_table}")


def cmd_doctor(args: argparse.Namespace) -> int:
    key = os.environ.get("ORION_USER_KEY") or os.environ.get("ORION_API_KEY")
    print("Orion EWS ingest doctor")
    print(f"  ORION_USER_KEY set: {bool(os.environ.get('ORION_USER_KEY'))}")
    print(f"  ORION_API_KEY set:  {bool(os.environ.get('ORION_API_KEY'))}")
    if key:
        print(f"  key preview:       {redact_secret(key)}")
    else:
        print("  → cole só a User Key (não a senha) em ORION_USER_KEY")
    print(f"  base URL: https://api.oriondata.io/api")
    print(f"  BQ target (proposed): climaticsystem.efc_clima source=orion")
    print(f"  GCS legacy path: gs://efc-clima-data/obs/ews/")
    if args.ping and key:
        try:
            client = OrionClient.from_env()
            client.refresh_token()
            devices = client.list_devices()
            print(f"  ping OK — {len(devices)} devices visible")
        except (OrionAuthError, Exception) as e:  # noqa: BLE001
            print(f"  ping FAIL — {type(e).__name__}: {e}")
            return 1
    elif args.ping:
        print("  ping skipped (sem chave)")
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Orion Data (EWS) → EFC climaticsystem ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="Check env / optional API ping")
    d.add_argument("--ping", action="store_true", help="Call /token + /userDevices")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("catalog", help="List devices + sensors")
    c.add_argument("--out", default="/tmp/orion_devices.json")
    c.add_argument("--sensors-out", default=None)
    c.add_argument("--serial", default="", help="Comma-separated IMEI filter")
    c.add_argument("--fixture", default=None, help="Local JSON (no API call)")
    c.set_defaults(func=cmd_catalog)

    r = sub.add_parser("readings", help="Pull sensor time series")
    r.add_argument("--out", default="/tmp/orion_readings.ndjson")
    r.add_argument("--clima-out", default=None)
    r.add_argument("--days", type=int, default=7)
    r.add_argument("--start", default=None)
    r.add_argument("--end", default=None)
    r.add_argument("--serial", default="")
    r.add_argument("--validated", action="store_true", help="Use /validatedSensorData")
    r.add_argument("--soft-only", action="store_true")
    r.add_argument("--raw-only", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--fixture-devices", default=None)
    r.add_argument("--fixture-readings", default=None)
    r.add_argument("--bq-project", default=None, help="e.g. climaticsystem")
    r.add_argument("--bq-dataset", default="efc_clima")
    r.set_defaults(func=cmd_readings)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except OrionAuthError as e:
        print(f"auth error: {e}", file=sys.stderr)
        print("Cole só a User Key (não a senha) em ORION_USER_KEY.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
