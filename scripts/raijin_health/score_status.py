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
    if not report["goes_ready"]:
        report["blocker"] = "GOES ABI ausente (data/goes_abi vazio)"
        report["next_steps"] = [
            "Rodar ml/goes_threshold/download_goes_abi.py para dias recentes",
            "Rebuild features (build_cascade_dataset / extract_features)",
            "python gcp/raijin_operational_score.py --day-start … --day-end … --load-bq",
            "Criar Cloud Scheduler diário para o score",
        ]
    elif not report["features_v2_exists"]:
        report["blocker"] = "CSV de features ausente"
        report["next_steps"] = ["Rebuild features a partir de GOES+GFS+EWS"]
    else:
        report["blocker"] = (
            "Features existem mas cobrem só até ~2026-05; rebuild necessário "
            "antes de religar score diário"
        )
        report["next_steps"] = [
            "Rebuild features com GOES+GFS+EWS para janela recente",
            "Score + load BQ raijin_alerts",
            "Agendar job diário",
        ]

    print(json.dumps(report, indent=2 if args.pretty else None))
    # Exit 0 always for diagnostic; blocker is in JSON
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
