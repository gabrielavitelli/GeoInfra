#!/usr/bin/env python3
"""Baixa MERGE/CPTEC (últimos N dias) e espelha em gs://efc-clima-data/obs/merge/grib/.

Usado como rede de segurança quando o Cloud Run falha no download direto do CPTEC.
O job efc-ingestion-merge passa a consultar esse cache via download_merge_efc_last3d.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_URL = "https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY"
GCS_PREFIX = os.environ.get("MERGE_GCS_PREFIX", "gs://efc-clima-data/obs/merge/grib")


def today_br() -> date:
    return datetime_now_br().date()


def datetime_now_br():
    from datetime import datetime

    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def download_http(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; EFC-merge-stage/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            dest.write_bytes(resp.read())
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:
        print(f"  HTTP falhou: {exc}")
        return False


def upload_gcs(local: Path, uri: str) -> None:
    from google.cloud import storage

    _, _, rest = uri.partition("gs://")
    bucket_name, _, blob_name = rest.partition("/")
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(str(local))
    print(f"  staged {uri} ({local.stat().st_size} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--out-dir", type=Path, default=Path("/tmp/merge_grib_stage"))
    ap.add_argument("--gcs-prefix", default=GCS_PREFIX)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ref = today_br()
    ok = 0
    for i in range(args.days):
        d = ref - timedelta(days=i)
        name = f"MERGE_CPTEC_{d.strftime('%Y%m%d')}.grib2"
        url = f"{BASE_URL}/{d.year}/{d.month:02d}/{name}"
        dest = args.out_dir / name
        print(f"Baixando {name} ...")
        if not download_http(url, dest):
            dest.unlink(missing_ok=True)
            continue
        uri = f"{args.gcs_prefix.rstrip('/')}/{name}"
        if args.dry_run:
            print(f"  dry-run skip upload {uri}")
        else:
            upload_gcs(dest, uri)
        ok += 1
    print(f"OK: {ok}/{args.days} GRIBs")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
