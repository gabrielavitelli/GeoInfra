#!/usr/bin/env python3
"""Pull ZENTRA Cloud devices + readings → GCS / BigQuery (efc_clima).

Cheap 6h path:
  - Cloud Run Job with baked image (not Cloud Build every run)
  - ``since_reading_id`` cursors in gs://efc-clima-data/obs/zentra/zentra_cursors.json
  - Skip empty/stale devices quickly

Smoke (requires token in env / Secret Manager — never argv):
  export ZENTRA_API_TOKEN='…'
  python3 -m scripts.zentra.ingest doctor --ping
  python3 -m scripts.zentra.ingest smoke --bq-project climaticsystem \\
    --gcs-prefix gs://efc-clima-data/obs/zentra/
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.zentra.client import (  # noqa: E402
    ZentraAuthError,
    ZentraClient,
    device_status,
    redact_secret,
    sort_devices_for_ingest,
)
from scripts.zentra.mapping import (  # noqa: E402
    PRIORITY_DEVICE_IDS,
    SOURCE_ZENTRA,
    clima_series_payload,
    device_asset_class,
    device_to_location_row,
    location_id_from_device,
    map_measurement,
    max_reading_id,
    reading_to_clima_row,
    station_id_from_device,
)
from scripts.zentra.secrets import key_fingerprint, load_zentra_api_token  # noqa: E402

DEFAULT_GCS_PREFIX = "gs://efc-clima-data/obs/zentra/"
DEFAULT_BOOTSTRAP_WINDOW = "24h"
DEFAULT_DEVICE_PAUSE_S = 1.25


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
    fieldnames: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {
                k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                for k, v in r.items()
            }
            w.writerow(flat)


def _ensure_token(args: argparse.Namespace) -> None:
    if getattr(args, "fixture", None) or getattr(args, "fixture_devices", None):
        return
    if os.environ.get("ZENTRA_API_TOKEN") or os.environ.get("ZENTRA_API_KEY"):
        return
    if getattr(args, "from_secret", True):
        val = load_zentra_api_token(
            project=getattr(args, "gcp_project", "climaticsystem"),
            secret=getattr(args, "secret_name", "zentra-api-token"),
        )
        print(f"[secret] loaded zentra-api-token ({key_fingerprint(val or '')})", flush=True)


def _parse_gcs_prefix(prefix: str) -> tuple[str, str]:
    assert prefix.startswith("gs://"), "--gcs-prefix must be gs://..."
    _, _, rest = prefix.partition("gs://")
    bucket_name, _, path = rest.partition("/")
    if path and not path.endswith("/"):
        path = path + "/"
    return bucket_name, path


def _gcs_client(project: Optional[str]):
    from google.cloud import storage  # type: ignore

    return storage.Client(project=project or "climaticsystem")


def _load_cursors_gcs(args: argparse.Namespace) -> dict[str, Any]:
    """Return {device_id: {since_reading_id, updated_at, ...}}."""
    if not getattr(args, "gcs_prefix", None):
        local = getattr(args, "cursors_file", None)
        if local and Path(local).exists():
            data = _load_json(Path(local))
            return data.get("cursors") if isinstance(data, dict) and "cursors" in data else data
        return {}
    try:
        bucket_name, path = _parse_gcs_prefix(args.gcs_prefix)
        client = _gcs_client(args.bq_project or args.gcp_project)
        blob = client.bucket(bucket_name).blob(f"{path}zentra_cursors.json")
        if not blob.exists():
            return {}
        data = json.loads(blob.download_as_text())
        if isinstance(data, dict) and "cursors" in data:
            return data["cursors"] or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        print(f"[cursors] load failed ({type(e).__name__}: {e}) — starting empty", flush=True)
        return {}


def _save_cursors(
    args: argparse.Namespace,
    cursors: dict[str, Any],
) -> None:
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cursors": cursors,
    }
    local = getattr(args, "cursors_file", None)
    if local:
        _write_json(Path(local), payload)
        print(f"[cursors] wrote {local} ({len(cursors)} devices)", flush=True)
    if not getattr(args, "gcs_prefix", None):
        return
    try:
        bucket_name, path = _parse_gcs_prefix(args.gcs_prefix)
        client = _gcs_client(args.bq_project or args.gcp_project)
        blob = client.bucket(bucket_name).blob(f"{path}zentra_cursors.json")
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        blob.upload_from_string(body, content_type="application/json")
        print(f"[cursors] gs://{bucket_name}/{path}zentra_cursors.json ({len(cursors)} devices)")
    except Exception as e:  # noqa: BLE001
        print(f"[cursors] save failed ({type(e).__name__}: {e})", file=sys.stderr)


def cmd_catalog(args: argparse.Namespace) -> int:
    if args.fixture:
        devices = _load_json(Path(args.fixture))
        if isinstance(devices, dict) and "devices" in devices:
            devices = devices["devices"]
        print(f"[fixture] {len(devices)} devices from {args.fixture}")
    else:
        _ensure_token(args)
        client = ZentraClient.from_env()
        print(f"[auth] ZENTRA_API_TOKEN={redact_secret(client.api_token)}", flush=True)
        devices = client.list_devices()
        print(f"[catalog] {len(devices)} devices", flush=True)

    devices = sort_devices_for_ingest(devices, priority_ids=PRIORITY_DEVICE_IDS)
    locations = [device_to_location_row(d) for d in devices]
    summary = []
    for d in devices:
        ports = d.get("ports") or []
        mapped = sorted(
            {
                v
                for p in ports
                # catalog maps by sensor model presence (measurement unknown until data)
                for v in (
                    ["soil_vwc"]
                    if "TEROS 12" in str(p.get("sensor_short_name") or "")
                    else ["water_potential_kpa"]
                    if "TEROS 21" in str(p.get("sensor_short_name") or "")
                    else ["precip_mm", "temp_c", "rh_pct"]
                    if "ATMOS" in str(p.get("sensor_short_name") or "").upper()
                    else []
                )
            }
        )
        summary.append(
            {
                "station_id": station_id_from_device(d),
                "location_id": location_id_from_device(d),
                "name": d.get("name"),
                "model": d.get("model"),
                "status": device_status(d),
                "asset_class": device_asset_class(d),
                "lat": (d.get("location") or {}).get("latitude"),
                "lon": (d.get("location") or {}).get("longitude"),
                "n_ports": len(ports),
                "sensors": sorted(
                    {
                        str(p.get("sensor_short_name") or "")
                        for p in ports
                        if p.get("sensor_short_name")
                    }
                ),
                "mapped_variables_expected": mapped,
                "last_measurement": (d.get("data_range") or {}).get("last_measurement"),
                "priority": station_id_from_device(d) in PRIORITY_DEVICE_IDS,
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
                "source": SOURCE_ZENTRA,
                "devices": devices,
                "locations": locations,
                "summary": summary,
            },
        )
    print(f"wrote {out}")
    for s in summary:
        print(
            f"  - {s['station_id']}: [{s['status']}] {s.get('asset_class')} "
            f"ports={s['n_ports']} sensors={s['sensors']} "
            f"lat={s['lat']} lon={s['lon']}"
        )

    if getattr(args, "bq_project", None):
        _maybe_load_bq_devices(args, locations)
    if getattr(args, "gcs_prefix", None):
        _upload_gcs_catalog(args, devices=devices, locations=locations, summary=summary)
    return 0


def _should_skip_device(device: dict[str, Any], args: argparse.Namespace) -> Optional[str]:
    status = device_status(device, stale_days=float(args.stale_days))
    if status == "empty" and not args.include_empty:
        return "empty"
    if status == "stale" and args.skip_stale:
        return "stale"
    if not device.get("can_read_data", True):
        return "no_read"
    return None


def cmd_readings(args: argparse.Namespace) -> int:
    source = getattr(args, "source", SOURCE_ZENTRA) or SOURCE_ZENTRA
    if args.fixture_devices:
        devices = _load_json(Path(args.fixture_devices))
        if isinstance(devices, dict) and "devices" in devices:
            devices = devices["devices"]
    else:
        _ensure_token(args)
        client = ZentraClient.from_env()
        devices = client.list_devices()

    only = {s.strip() for s in (args.device or "").split(",") if s.strip()}
    if only:
        devices = [d for d in devices if d.get("device_id") in only]

    devices = sort_devices_for_ingest(devices, priority_ids=PRIORITY_DEVICE_IDS)
    cursors = _load_cursors_gcs(args)
    print(
        f"[readings] devices={len(devices)} cursors={len(cursors)} "
        f"bootstrap_window={args.bootstrap_window}",
        flush=True,
    )

    staging: list[dict[str, Any]] = []
    clima: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    fetched_devices = 0
    new_cursors = dict(cursors)

    if args.dry_run:
        print("[dry-run] skipping /data calls")
        devices_for_bq = devices
    else:
        client = ZentraClient.from_env()
        for i, device in enumerate(devices):
            did = str(device.get("device_id") or "")
            reason = _should_skip_device(device, args)
            if reason:
                skipped.append({"device_id": did, "reason": reason})
                print(f"  skip {did} ({reason})", flush=True)
                continue

            cur = cursors.get(did) or {}
            since = cur.get("since_reading_id")
            if since is not None:
                since = int(since)
            print(
                f"  [{i + 1}/{len(devices)}] {did} "
                f"since_reading_id={since if since is not None else '∅ (window=' + args.bootstrap_window + ')'}",
                flush=True,
            )
            try:
                if since is not None:
                    values = client.fetch_device_data(did, since_reading_id=since)
                else:
                    values = client.fetch_device_data(did, window=args.bootstrap_window)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {did} fetch FAIL {type(e).__name__}: {e}", flush=True)
                skipped.append({"device_id": did, "reason": f"error:{type(e).__name__}"})
                time.sleep(float(args.device_pause_s))
                continue

            fetched_devices += 1
            max_rid = max_reading_id(values)
            # If since_reading_id returned nothing, keep prior cursor; else advance.
            if max_rid is not None:
                new_cursors[did] = {
                    "since_reading_id": max_rid,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "n_values_last_run": len(values),
                }
            elif since is None:
                # Bootstrap empty — still record so we don't re-pull full window forever.
                # Use reading_id_max from a tiny since=0? Prefer metadata via empty page.
                # Leave unset; next run will bootstrap again (stale/empty ok).
                pass

            mapped_n = 0
            for v in values:
                staging.append(
                    {
                        "device_id": did,
                        "station_id": station_id_from_device(device),
                        "location_id": location_id_from_device(device),
                        "port_num": v.get("port_num"),
                        "sensor_name": v.get("sensor_name"),
                        "measurement": v.get("measurement"),
                        "unit": v.get("unit"),
                        "value": v.get("value"),
                        "datetime": v.get("datetime"),
                        "timestamp": v.get("timestamp"),
                        "reading_id": v.get("reading_id"),
                        "error_code": v.get("error_code"),
                        "position": v.get("position"),
                        "mapped_variable": map_measurement(
                            v.get("sensor_name"), v.get("measurement"), v.get("unit")
                        ),
                    }
                )
                row = reading_to_clima_row(device=device, value_row=v, source=source)
                if row:
                    clima.append(row)
                    mapped_n += 1
            print(
                f"    values={len(values)} mapped={mapped_n} max_reading_id={max_rid}",
                flush=True,
            )
            time.sleep(float(args.device_pause_s))

        devices_for_bq = devices

    out = Path(args.out)
    if out.suffix.lower() == ".csv":
        _write_csv(out, staging)
    else:
        _write_ndjson(out, staging)
    clima_out = (
        Path(args.clima_out) if args.clima_out else out.with_name(out.stem + "_clima_series.ndjson")
    )
    _write_ndjson(clima_out, clima_series_payload(clima))
    print(
        f"wrote staging={out} ({len(staging)} rows) clima={clima_out} ({len(clima)} rows) "
        f"fetched_devices={fetched_devices} skipped={len(skipped)}"
    )

    locations = [device_to_location_row(d, source=source) for d in devices_for_bq]
    _save_cursors(args, new_cursors)

    if getattr(args, "gcs_prefix", None):
        _upload_gcs(
            args,
            devices=devices_for_bq,
            staging=staging,
            clima=clima,
            locations=locations,
            skipped=skipped,
        )
    if args.bq_project:
        _maybe_load_bq(args, clima, locations=locations, staging=staging)
    return 0


def _upload_gcs_catalog(
    args: argparse.Namespace,
    *,
    devices: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    summary: list[dict[str, Any]],
) -> None:
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        print("[gcs] google-cloud-storage ausente — pulando upload", file=sys.stderr)
        return
    bucket_name, path = _parse_gcs_prefix(args.gcs_prefix)
    client = storage.Client(project=args.bq_project or args.gcp_project or "climaticsystem")
    bucket = client.bucket(bucket_name)
    files = {
        f"{path}zentra_devices_latest.json": json.dumps(
            {
                "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
                "devices": devices,
                "summary": summary,
            },
            ensure_ascii=False,
            default=str,
        ),
        f"{path}zentra_locations_latest.json": json.dumps(
            locations, ensure_ascii=False, default=str
        ),
    }
    for blob_name, body in files.items():
        bucket.blob(blob_name).upload_from_string(body, content_type="application/json")
        print(f"[gcs] gs://{bucket_name}/{blob_name} ({len(body)} bytes)")


def _upload_gcs(
    args: argparse.Namespace,
    *,
    devices: list[dict[str, Any]],
    staging: list[dict[str, Any]],
    clima: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    skipped: list[dict[str, str]],
) -> None:
    try:
        from google.cloud import storage  # type: ignore
    except ImportError:
        print("[gcs] google-cloud-storage ausente — pulando upload", file=sys.stderr)
        return

    bucket_name, path = _parse_gcs_prefix(args.gcs_prefix)
    client = storage.Client(project=args.bq_project or args.gcp_project or "climaticsystem")
    bucket = client.bucket(bucket_name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    files = {
        f"{path}zentra_devices_latest.json": json.dumps(
            {"pulled_at_utc": datetime.now(timezone.utc).isoformat(), "devices": devices},
            ensure_ascii=False,
            default=str,
        ),
        f"{path}zentra_locations_latest.json": json.dumps(
            locations, ensure_ascii=False, default=str
        ),
        f"{path}zentra_readings_latest.ndjson": "\n".join(
            json.dumps(r, ensure_ascii=False, default=str) for r in staging
        )
        + ("\n" if staging else ""),
        f"{path}zentra_clima_series_latest.ndjson": "\n".join(
            json.dumps(r, ensure_ascii=False, default=str) for r in clima_series_payload(clima)
        )
        + ("\n" if clima else ""),
        f"{path}runs/{stamp}/summary.json": json.dumps(
            {
                "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
                "n_devices": len(devices),
                "n_staging": len(staging),
                "n_clima": len(clima),
                "skipped": skipped,
                "sample_station_ids": [station_id_from_device(d) for d in devices[:12]],
            },
            ensure_ascii=False,
            indent=2,
        ),
    }
    for blob_name, body in files.items():
        bucket.blob(blob_name).upload_from_string(body, content_type="application/json")
        print(f"[gcs] gs://{bucket_name}/{blob_name} ({len(body)} bytes)")


def _maybe_load_bq_devices(args: argparse.Namespace, locations: list[dict[str, Any]]) -> None:
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        print("[bq] google-cloud-bigquery ausente — pulando", file=sys.stderr)
        return
    _load_zentra_devices_table(args, locations, bigquery)


def _load_zentra_devices_table(args: argparse.Namespace, locations: list[dict[str, Any]], bigquery) -> None:
    project = args.bq_project
    dataset = args.bq_dataset
    client = bigquery.Client(project=project)
    loc_payload = []
    for loc in locations:
        extra = loc.get("extra") or {}
        loc_payload.append(
            {
                "source": loc["source"],
                "location_id": loc.get("location_id"),
                "station_id": loc["station_id"],
                "name": loc["name"],
                "lat": loc["lat"],
                "lon": loc["lon"],
                "status": extra.get("status"),
                "model": extra.get("model"),
                "device_type": extra.get("device_type"),
                "organization_name": extra.get("organization_name"),
                "n_ports": extra.get("n_ports"),
                "sensors": extra.get("sensors"),
                "last_measurement": extra.get("last_measurement"),
                "asset_class": extra.get("asset_class"),
                "zentra_device_id": extra.get("zentra_device_id"),
                "pulled_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    devices_table = f"{project}.{dataset}.zentra_devices"
    job = client.load_table_from_json(
        loc_payload,
        devices_table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True,
        ),
    )
    job.result()
    print(f"[bq] {devices_table} replaced ({job.output_rows} rows)")


def _maybe_load_bq(
    args: argparse.Namespace,
    clima_rows: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    staging: list[dict[str, Any]],
) -> None:
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

    if getattr(args, "load_staging", False) and staging:
        staging_table = f"{project}.{dataset}.zentra_readings_staging"
        print(f"[bq] loading {len(staging)} rows → {staging_table} (append)")
        job_s = client.load_table_from_json(
            staging,
            staging_table,
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                autodetect=True,
            ),
        )
        job_s.result()
        print(f"[bq] staging append ok ({job_s.output_rows} rows)")

    _load_zentra_devices_table(args, locations, bigquery)


def cmd_doctor(args: argparse.Namespace) -> int:
    print("ZENTRA Cloud ingest doctor")
    key = os.environ.get("ZENTRA_API_TOKEN") or os.environ.get("ZENTRA_API_KEY")
    if not key and getattr(args, "from_secret", True):
        try:
            key = load_zentra_api_token(
                project=args.gcp_project,
                secret=args.secret_name,
                allow_missing=True,
            )
            if key:
                print(
                    f"  secret:            {args.secret_name}@{args.gcp_project} "
                    f"({key_fingerprint(key)})"
                )
        except Exception as e:  # noqa: BLE001
            print(f"  secret load:       FAIL ({type(e).__name__}: {e})")
    print(f"  ZENTRA_API_TOKEN set: {bool(os.environ.get('ZENTRA_API_TOKEN'))}")
    if key:
        print(f"  key preview:       {redact_secret(key)}")
    else:
        print("  → defina ZENTRA_API_TOKEN ou autentique gcloud para ler zentra-api-token")
    print("  base URL: https://api.zentracloud.io")
    print("  BQ target: climaticsystem.efc_clima (source=zentra, location_id=zentra_*)")
    print(f"  GCS path:  {DEFAULT_GCS_PREFIX}")
    if args.ping and key:
        try:
            client = ZentraClient.from_env()
            devices = client.list_devices()
            print(f"  ping OK — {len(devices)} devices visible")
            for d in sort_devices_for_ingest(devices, priority_ids=PRIORITY_DEVICE_IDS)[:12]:
                print(
                    f"    - {station_id_from_device(d)}: [{device_status(d)}] "
                    f"{device_asset_class(d)} "
                    f"lat={(d.get('location') or {}).get('latitude')} "
                    f"lon={(d.get('location') or {}).get('longitude')} "
                    f"ports={len(d.get('ports') or [])}"
                )
        except (ZentraAuthError, Exception) as e:  # noqa: BLE001
            print(f"  ping FAIL — {type(e).__name__}: {e}")
            return 1
    elif args.ping:
        print("  ping skipped (sem chave)")
        return 2
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """End-to-end: secret → catalog → incremental readings → optional BQ/GCS."""
    args.from_secret = True
    rc = cmd_doctor(
        argparse.Namespace(
            ping=True,
            from_secret=True,
            gcp_project=args.gcp_project,
            secret_name=args.secret_name,
        )
    )
    if rc != 0:
        return rc
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    cat_args = argparse.Namespace(
        fixture=None,
        out=args.out_dir.rstrip("/") + "/zentra_devices.json",
        from_secret=True,
        gcp_project=args.gcp_project,
        secret_name=args.secret_name,
        bq_project=args.bq_project,
        bq_dataset=args.bq_dataset,
        gcs_prefix=args.gcs_prefix,
    )
    rc = cmd_catalog(cat_args)
    if rc != 0:
        return rc
    read_args = argparse.Namespace(
        fixture_devices=None,
        out=args.out_dir.rstrip("/") + "/zentra_readings.ndjson",
        clima_out=args.out_dir.rstrip("/") + "/zentra_clima_series.ndjson",
        device=args.device,
        bootstrap_window=args.bootstrap_window,
        stale_days=args.stale_days,
        skip_stale=args.skip_stale,
        include_empty=False,
        device_pause_s=args.device_pause_s,
        dry_run=False,
        bq_project=args.bq_project,
        bq_dataset=args.bq_dataset,
        gcs_prefix=args.gcs_prefix,
        cursors_file=args.out_dir.rstrip("/") + "/zentra_cursors.json",
        load_staging=args.load_staging,
        source=args.source,
        from_secret=True,
        gcp_project=args.gcp_project,
        secret_name=args.secret_name,
    )
    return cmd_readings(read_args)


def cmd_run(args: argparse.Namespace) -> int:
    """Cloud Run Job entry: catalog + incremental readings → BQ/GCS."""
    args.out_dir = args.out_dir or "/tmp/zentra_run"
    args.device = args.device or ""
    args.bootstrap_window = args.bootstrap_window or DEFAULT_BOOTSTRAP_WINDOW
    args.stale_days = args.stale_days if args.stale_days is not None else 14.0
    args.skip_stale = bool(args.skip_stale)
    args.device_pause_s = args.device_pause_s if args.device_pause_s is not None else DEFAULT_DEVICE_PAUSE_S
    args.load_staging = bool(args.load_staging)
    args.source = args.source or SOURCE_ZENTRA
    args.bq_project = args.bq_project or os.environ.get("BQ_PROJECT") or "climaticsystem"
    args.bq_dataset = args.bq_dataset or os.environ.get("BQ_DATASET") or "efc_clima"
    args.gcs_prefix = args.gcs_prefix or os.environ.get("GCS_PREFIX") or DEFAULT_GCS_PREFIX
    args.gcp_project = args.gcp_project or os.environ.get("GCP_PROJECT") or "climaticsystem"
    args.secret_name = args.secret_name or "zentra-api-token"
    args.from_secret = not bool(os.environ.get("ZENTRA_API_TOKEN"))
    return cmd_smoke(args)


def _add_secret_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--gcp-project", default="climaticsystem")
    ap.add_argument("--secret-name", default="zentra-api-token")
    ap.add_argument(
        "--no-from-secret",
        dest="from_secret",
        action="store_false",
        help="Do not call Secret Manager (env only)",
    )
    ap.set_defaults(from_secret=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ZENTRA Cloud → EFC climaticsystem ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="Check env / optional API ping")
    d.add_argument("--ping", action="store_true")
    _add_secret_args(d)
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("catalog", help="List devices + ports")
    c.add_argument("--out", default="/tmp/zentra_devices.json")
    c.add_argument("--fixture", default=None)
    c.add_argument("--bq-project", default=None)
    c.add_argument("--bq-dataset", default="efc_clima")
    c.add_argument("--gcs-prefix", default=None)
    _add_secret_args(c)
    c.set_defaults(func=cmd_catalog)

    r = sub.add_parser("readings", help="Pull incremental device data")
    r.add_argument("--out", default="/tmp/zentra_readings.ndjson")
    r.add_argument("--clima-out", default=None)
    r.add_argument("--device", default="", help="Comma-separated device_id filter")
    r.add_argument("--bootstrap-window", default=DEFAULT_BOOTSTRAP_WINDOW)
    r.add_argument("--stale-days", type=float, default=14.0)
    r.add_argument(
        "--skip-stale",
        action="store_true",
        help="Skip devices with last_measurement older than --stale-days",
    )
    r.add_argument("--include-empty", action="store_true")
    r.add_argument("--device-pause-s", type=float, default=DEFAULT_DEVICE_PAUSE_S)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--fixture-devices", default=None)
    r.add_argument("--bq-project", default=None)
    r.add_argument("--bq-dataset", default="efc_clima")
    r.add_argument("--gcs-prefix", default=None, help=f"e.g. {DEFAULT_GCS_PREFIX}")
    r.add_argument("--cursors-file", default=None, help="Local cursor JSON path")
    r.add_argument("--load-staging", action="store_true")
    r.add_argument("--source", default=SOURCE_ZENTRA)
    _add_secret_args(r)
    r.set_defaults(func=cmd_readings)

    s = sub.add_parser("smoke", help="Secret → catalog → readings → optional BQ/GCS")
    s.add_argument("--out-dir", default="/tmp/zentra_smoke")
    s.add_argument("--device", default="")
    s.add_argument("--bootstrap-window", default=DEFAULT_BOOTSTRAP_WINDOW)
    s.add_argument("--stale-days", type=float, default=14.0)
    s.add_argument("--skip-stale", action="store_true")
    s.add_argument("--device-pause-s", type=float, default=DEFAULT_DEVICE_PAUSE_S)
    s.add_argument("--bq-project", default=None)
    s.add_argument("--bq-dataset", default="efc_clima")
    s.add_argument("--gcs-prefix", default=None)
    s.add_argument("--load-staging", action="store_true")
    s.add_argument("--source", default=SOURCE_ZENTRA)
    _add_secret_args(s)
    s.set_defaults(func=cmd_smoke)

    run = sub.add_parser("run", help="Cloud Run Job entrypoint (smoke with BQ/GCS defaults)")
    run.add_argument("--out-dir", default="/tmp/zentra_run")
    run.add_argument("--device", default="")
    run.add_argument("--bootstrap-window", default=DEFAULT_BOOTSTRAP_WINDOW)
    run.add_argument("--stale-days", type=float, default=14.0)
    run.add_argument("--skip-stale", action="store_true", default=True)
    run.add_argument("--no-skip-stale", dest="skip_stale", action="store_false")
    run.add_argument("--device-pause-s", type=float, default=DEFAULT_DEVICE_PAUSE_S)
    run.add_argument("--bq-project", default=None)
    run.add_argument("--bq-dataset", default="efc_clima")
    run.add_argument("--gcs-prefix", default=None)
    run.add_argument("--load-staging", action="store_true")
    run.add_argument("--source", default=SOURCE_ZENTRA)
    _add_secret_args(run)
    run.set_defaults(func=cmd_run)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ZentraAuthError as e:
        print(f"auth error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
