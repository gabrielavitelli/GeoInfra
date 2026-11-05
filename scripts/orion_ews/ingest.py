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
    clima_series_payload,
    device_to_location_row,
    map_sensor_type,
    reading_to_clima_row,
    station_id_from_device,
)
from scripts.orion_ews.secrets import (  # noqa: E402
    key_fingerprint,
    load_orion_user_key,
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


def _ensure_key(args: argparse.Namespace) -> None:
    if getattr(args, "fixture", None) or getattr(args, "fixture_devices", None):
        return
    if os.environ.get("ORION_USER_KEY") or os.environ.get("ORION_API_KEY"):
        return
    if getattr(args, "from_secret", True):
        val = load_orion_user_key(
            project=getattr(args, "gcp_project", "climaticsystem"),
            secret=getattr(args, "secret_name", "orion-user-key"),
        )
        print(f"[secret] loaded orion-user-key ({key_fingerprint(val)})", flush=True)


def cmd_catalog(args: argparse.Namespace) -> int:
    if args.fixture:
        devices = _load_json(Path(args.fixture))
        print(f"[fixture] {len(devices)} devices from {args.fixture}")
    else:
        _ensure_key(args)
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
    source = getattr(args, "source", SOURCE_ORION) or SOURCE_ORION
    if args.fixture_devices:
        devices = _load_json(Path(args.fixture_devices))
        if isinstance(devices, dict) and "devices" in devices:
            devices = devices["devices"]
    else:
        _ensure_key(args)
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
        row = reading_to_clima_row(
            device=device or meta,
            sensor_meta=meta,
            reading=reading,
            source=source,
        )
        if row:
            clima.append(row)

    out = Path(args.out)
    if out.suffix.lower() == ".csv":
        _write_csv(out, staging)
    else:
        _write_ndjson(out, staging)
    clima_out = Path(args.clima_out) if args.clima_out else out.with_name(out.stem + "_clima_series.ndjson")
    _write_ndjson(clima_out, clima_series_payload(clima))
    print(f"wrote staging={out} ({len(staging)} rows) clima={clima_out} ({len(clima)} rows)")

    locations = [device_to_location_row(d, source=source) for d in devices]
    if getattr(args, "gcs_prefix", None):
        _upload_gcs(args, devices=devices, staging=staging, clima=clima, locations=locations)
    if args.bq_project:
        _maybe_load_bq(args, clima, locations=locations)
    return 0


def _upload_gcs(
    args: argparse.Namespace,
    *,
    devices: list[dict[str, Any]],
    staging: list[dict[str, Any]],
    clima: list[dict[str, Any]],
    locations: list[dict[str, Any]],
) -> None:
    """Write catalog + consolidated timeseries under gs://efc-clima-data/obs/ews/."""
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        print("[gcs] google-cloud-storage ausente — pulando upload", file=sys.stderr)
        return

    prefix = args.gcs_prefix.rstrip("/") + "/"
    # gs://bucket/path/
    assert prefix.startswith("gs://"), "--gcs-prefix must be gs://..."
    _, _, rest = prefix.partition("gs://")
    bucket_name, _, path = rest.partition("/")
    client = storage.Client(project=args.bq_project or args.gcp_project or "climaticsystem")
    bucket = client.bucket(bucket_name)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files = {
        f"{path}orion_devices_latest.json": json.dumps(
            {"pulled_at_utc": datetime.now(timezone.utc).isoformat(), "devices": devices},
            ensure_ascii=False,
            default=str,
        ),
        f"{path}orion_locations_latest.json": json.dumps(locations, ensure_ascii=False, default=str),
        f"{path}orion_readings_latest.ndjson": "\n".join(
            json.dumps(r, ensure_ascii=False, default=str) for r in staging
        )
        + ("\n" if staging else ""),
        f"{path}orion_clima_series_latest.ndjson": "\n".join(
            json.dumps(r, ensure_ascii=False, default=str) for r in clima_series_payload(clima)
        )
        + ("\n" if clima else ""),
        f"{path}runs/{stamp}/summary.json": json.dumps(
            {
                "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
                "n_devices": len(devices),
                "n_staging": len(staging),
                "n_clima": len(clima),
                "sample_station_ids": [
                    station_id_from_device(d) for d in devices[:10]
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
    }
    for blob_name, body in files.items():
        blob = bucket.blob(blob_name)
        blob.upload_from_string(body, content_type="application/json")
        print(f"[gcs] gs://{bucket_name}/{blob_name} ({len(body)} bytes)")


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
    series_table = f"{project}.{dataset}.clima_series"

    series_payload = clima_series_payload(clima_rows)
    print(f"[bq] loading {len(series_payload)} rows → {series_table} (append)")
    if series_payload:
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

    loc_payload = []
    for loc in locations:
        loc_payload.append(
            {
                "source": loc["source"],
                "location_id": loc.get("location_id"),
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
    devices_table = f"{project}.{dataset}.orion_devices"
    job2 = client.load_table_from_json(
        loc_payload,
        devices_table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        ),
    )
    job2.result()
    print(f"[bq] {devices_table} replaced ({job2.output_rows} rows)")


def cmd_doctor(args: argparse.Namespace) -> int:
    print("Orion EWS ingest doctor")
    key = os.environ.get("ORION_USER_KEY") or os.environ.get("ORION_API_KEY")
    if not key and getattr(args, "from_secret", True):
        try:
            key = load_orion_user_key(
                project=args.gcp_project,
                secret=args.secret_name,
                allow_missing=True,
            )
            if key:
                print(f"  secret:            {args.secret_name}@{args.gcp_project} ({key_fingerprint(key)})")
        except Exception as e:  # noqa: BLE001
            print(f"  secret load:       FAIL ({type(e).__name__}: {e})")
    print(f"  ORION_USER_KEY set: {bool(os.environ.get('ORION_USER_KEY'))}")
    if key:
        print(f"  key preview:       {redact_secret(key)}")
    else:
        print("  → defina ORION_USER_KEY ou autentique gcloud para ler orion-user-key")
    print("  base URL: https://api.oriondata.io/api")
    print("  BQ target: climaticsystem.efc_clima (source=orion, location_id=ews_*)")
    print("  GCS path:  gs://efc-clima-data/obs/ews/")
    if args.ping and key:
        try:
            client = OrionClient.from_env()
            client.refresh_token()
            devices = client.list_devices()
            print(f"  ping OK — {len(devices)} devices visible")
            for d in devices[:8]:
                print(
                    f"    - {station_id_from_device(d)}: {d.get('deviceName')} "
                    f"[{d.get('status')}] bat={d.get('batteryPercentage')}% "
                    f"imei={d.get('serialNumber')} lat={d.get('latitude')} lon={d.get('longitude')}"
                )
            codes = {station_id_from_device(d) for d in devices}
            print(f"  EWS6536 present: {'EWS6536' in codes}")
        except (OrionAuthError, Exception) as e:  # noqa: BLE001
            print(f"  ping FAIL — {type(e).__name__}: {e}")
            return 1
    elif args.ping:
        print("  ping skipped (sem chave)")
        return 2
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """End-to-end: secret → catalog → recent readings → optional BQ/GCS."""
    args.from_secret = True
    rc = cmd_doctor(argparse.Namespace(
        ping=True,
        from_secret=True,
        gcp_project=args.gcp_project,
        secret_name=args.secret_name,
    ))
    if rc != 0:
        return rc
    cat_args = argparse.Namespace(
        fixture=None,
        out=args.out_dir.rstrip("/") + "/orion_devices.json",
        sensors_out=args.out_dir.rstrip("/") + "/orion_sensors.csv",
        serial=args.serial,
        from_secret=True,
        gcp_project=args.gcp_project,
        secret_name=args.secret_name,
    )
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    rc = cmd_catalog(cat_args)
    if rc != 0:
        return rc
    read_args = argparse.Namespace(
        fixture_devices=None,
        fixture_readings=None,
        out=args.out_dir.rstrip("/") + "/orion_readings.ndjson",
        clima_out=args.out_dir.rstrip("/") + "/orion_clima_series.ndjson",
        days=args.days,
        start=None,
        end=None,
        serial=args.serial,
        validated=False,
        soft_only=False,
        raw_only=False,
        dry_run=False,
        bq_project=args.bq_project,
        bq_dataset=args.bq_dataset,
        gcs_prefix=args.gcs_prefix,
        source=args.source,
        from_secret=True,
        gcp_project=args.gcp_project,
        secret_name=args.secret_name,
    )
    return cmd_readings(read_args)


def _add_secret_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--gcp-project", default="climaticsystem")
    ap.add_argument("--secret-name", default="orion-user-key")
    ap.add_argument(
        "--no-from-secret",
        dest="from_secret",
        action="store_false",
        help="Do not call Secret Manager (env only)",
    )
    ap.set_defaults(from_secret=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Orion Data (EWS) → EFC climaticsystem ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="Check env / optional API ping")
    d.add_argument("--ping", action="store_true", help="Call /token + /userDevices")
    _add_secret_args(d)
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("catalog", help="List devices + sensors")
    c.add_argument("--out", default="/tmp/orion_devices.json")
    c.add_argument("--sensors-out", default=None)
    c.add_argument("--serial", default="", help="Comma-separated IMEI filter")
    c.add_argument("--fixture", default=None, help="Local JSON (no API call)")
    _add_secret_args(c)
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
    r.add_argument(
        "--gcs-prefix",
        default=None,
        help="e.g. gs://efc-clima-data/obs/ews/",
    )
    r.add_argument("--source", default=SOURCE_ORION, choices=["orion", "ews"])
    _add_secret_args(r)
    r.set_defaults(func=cmd_readings)

    s = sub.add_parser("smoke", help="Secret → catalog → readings → optional BQ/GCS")
    s.add_argument("--out-dir", default="/tmp/orion_smoke")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--serial", default="", help="Optional IMEI filter")
    s.add_argument("--bq-project", default=None)
    s.add_argument("--bq-dataset", default="efc_clima")
    s.add_argument("--gcs-prefix", default=None)
    s.add_argument("--source", default=SOURCE_ORION, choices=["orion", "ews"])
    _add_secret_args(s)
    s.set_defaults(func=cmd_smoke)

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
