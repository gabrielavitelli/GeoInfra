#!/usr/bin/env bash
# Local smoke after ADC / gcloud auth is ready.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="${HOME}/google-cloud-sdk-install/google-cloud-sdk/bin:${PATH}"
cd "$ROOT"
python3 -m scripts.zentra.ingest doctor --ping
python3 -m scripts.zentra.ingest smoke --skip-stale \
  --out-dir /tmp/zentra_smoke \
  --bq-project climaticsystem \
  --gcs-prefix gs://efc-clima-data/obs/zentra/
