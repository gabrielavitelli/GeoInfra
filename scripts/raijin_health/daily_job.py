#!/usr/bin/env python3
"""Cloud Run entrypoint: stage GOES ABI (ontem) → score → raijin_alerts → health JSON."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent


def _today_br() -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _stage_goes(day: date) -> int:
    """Baixa MCMIPC G19 (horas 0/6/12/18) → GCS trainkit."""
    import re
    from pathlib import Path as P

    import s3fs
    from google.cloud import storage

    fs = s3fs.S3FileSystem(anon=True)
    client = storage.Client()
    bucket = client.bucket(os.environ.get("GCS_BUCKET", "efc-clima-data"))
    doy = day.timetuple().tm_yday
    hours = (0, 6, 12, 18)
    ok = 0
    for h in hours:
        prefix = f"noaa-goes19/ABI-L2-MCMIPC/{day.year}/{doy:03d}/{h:02d}/"
        try:
            keys = fs.ls(prefix)
        except Exception as exc:
            print(f"GOES list fail {day} {h:02d}: {exc}")
            continue
        if not keys:
            print(f"GOES empty {day} {h:02d}")
            continue
        key = sorted(keys)[len(keys) // 2]
        name = P(key).name
        local = P(f"/tmp/goes_daily/{day.isoformat()}")
        local.mkdir(parents=True, exist_ok=True)
        dest = local / name
        if not dest.exists():
            print(f"GOES download {key}")
            fs.get(key, str(dest))
        blob = bucket.blob(
            f"ml/raijin-trainkit/data/goes_abi/{day.isoformat()}/{name}"
        )
        if not blob.exists():
            blob.upload_from_filename(str(dest))
            print(f"GOES staged {blob.name} ({dest.stat().st_size})")
        else:
            print(f"GOES exists {blob.name}")
        ok += 1
    return ok


def main() -> int:
    lookback = max(1, int(os.environ.get("RAIJIN_SCORE_LOOKBACK_DAYS", "3")))
    end = _today_br()
    # MERGE/GOES do dia civil BR costumam fechar à tarde — score até ontem por padrão
    if os.environ.get("RAIJIN_SCORE_INCLUDE_TODAY", "0") not in ("1", "true", "yes"):
        end = end - timedelta(days=1)
    start = end - timedelta(days=lookback - 1)

    print(f"Raijin daily job: GOES+score {start} → {end}")
    d = start
    goes_ok = 0
    while d <= end:
        goes_ok += _stage_goes(d)
        d += timedelta(days=1)
    if goes_ok == 0:
        print("ERRO: nenhum GOES baixado", file=sys.stderr)
        return 1

    score = [
        sys.executable,
        str(HERE / "operational_score.py"),
        "--day-start",
        start.isoformat(),
        "--day-end",
        end.isoformat(),
        "--load-bq",
        "--out-csv",
        "/tmp/raijin_preds_daily.csv",
    ]
    print("Exec", " ".join(score))
    r = subprocess.run(score, check=False)
    if r.returncode != 0:
        return r.returncode

    health = [
        sys.executable,
        str(HERE / "check.py"),
        "--gcs-out",
        os.environ.get(
            "RAIJIN_HEALTH_GCS",
            "gs://efc-clima-data/monitor/health/raijin_health.json",
        ),
    ]
    print("Exec", " ".join(health))
    return subprocess.run(health, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
