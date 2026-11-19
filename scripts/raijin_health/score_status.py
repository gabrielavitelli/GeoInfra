#!/usr/bin/env python3
"""Diagnóstico do path de scoring ML Raijin (GOES + features + modelo).

Não executa inferência — verifica pré-requisitos em GCS/BQ e imprime o bloqueio.
Quando features frescas existirem, use o ClimaticSystem:

  PYTHONPATH=.:$HOME/raijin-trainkit \
    python gcp/raijin_operational_score.py \
      --day-start YYYY-MM-DD --day-end YYYY-MM-DD --load-bq
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

PROJECT = os.environ.get("GCP_PROJECT", "climaticsystem")
BUCKET = os.environ.get("GCS_BUCKET", "efc-clima-data")
TRAINKIT_PREFIX = os.environ.get(
    "RAIJIN_TRAINKIT_PREFIX", "ml/raijin-trainkit"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    from google.cloud import storage

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)

    goes_blobs = [
        b.name
        for b in bucket.list_blobs(prefix=f"{TRAINKIT_PREFIX}/data/goes_abi/")
        if not b.name.endswith("/") and not b.name.endswith(".gitkeep.json")
    ]
    feat = bucket.blob(f"{TRAINKIT_PREFIX}/data/ml/cascade_v2/features_cascade_v2.csv")
    model = bucket.blob(f"{TRAINKIT_PREFIX}/data/ml/cascade_v2/cascade_v2_model.joblib")
    feat_exists = feat.exists()
    model_exists = model.exists()
    feat_updated = None
    if feat_exists:
        feat.reload()
        if feat.updated is not None:
            feat_updated = feat.updated.isoformat()

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "goes_abi_objects": len(goes_blobs),
        "goes_ready": len(goes_blobs) > 0,
        "features_v2_exists": feat_exists,
        "features_v2_updated": feat_updated,
        "model_v2_exists": model_exists,
        "blocker": None,
        "next_steps": [],
    }
    # Frescor operacional = tabela raijin_alerts (não o CSV histórico do trainkit).
    from google.cloud import bigquery

    bq = bigquery.Client(project=PROJECT)
    row = list(
        bq.query(
            f"""
            SELECT CAST(MAX(day) AS STRING) AS max_day,
                   COUNTIF(day >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AS n_7d
            FROM `{PROJECT}.efc_clima.raijin_alerts`
            """
        ).result()
    )[0]
    report["raijin_alerts_max_day"] = row["max_day"]
    report["raijin_alerts_n_7d"] = int(row["n_7d"] or 0)

    if not report["goes_ready"]:
        report["blocker"] = "GOES ABI ausente (data/goes_abi vazio)"
        report["next_steps"] = [
            "python scripts/raijin_health/daily_job.py  # baixa GOES + score",
            "Ou: deploy job raijin-score + scheduler diário",
        ]
    elif not report["model_v2_exists"]:
        report["blocker"] = "Modelo cascade_v2 ausente no GCS"
    elif not report["raijin_alerts_max_day"]:
        report["blocker"] = "raijin_alerts sem dias recentes — rode operational_score --load-bq"
        report["next_steps"] = [
            "python scripts/raijin_health/operational_score.py --day-start … --day-end … --load-bq"
        ]
    else:
        from datetime import date as _date

        lag = (_date.today() - _date.fromisoformat(str(report["raijin_alerts_max_day"])[:10])).days
        report["raijin_alerts_lag_days"] = lag
        if lag <= 2:
            report["blocker"] = None
            report["next_steps"] = []
        else:
            report["blocker"] = f"raijin_alerts defasado ({lag}d) — rode daily_job / scheduler"
            report["next_steps"] = ["gcloud run jobs execute raijin-score --region=us-central1 --wait"]

    print(json.dumps(report, indent=2 if args.pretty else None))
    return 0 if report["blocker"] is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
