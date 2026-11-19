#!/usr/bin/env bash
# After gcloud auth succeeds: load secret (no echo) → Orion smoke → BQ/GCS.
set -euo pipefail
export PATH="${HOME}/google-cloud-sdk-install/google-cloud-sdk/bin:${PATH:-}"
cd "$(dirname "$0")/../.."

echo "[wait] checking gcloud auth…"
if ! gcloud auth print-access-token >/dev/null 2>&1; then
  echo "gcloud ainda sem credenciais neste VM."
  echo "Abra a URL em /tmp/gcloud-oauth-url.txt, cole o verification code no chat."
  exit 2
fi

gcloud config set project climaticsystem >/dev/null
ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
echo "[ok] gcloud account=${ACCOUNT}"

# Load secret into env without printing value
ORION_USER_KEY="$(gcloud secrets versions access latest --secret=orion-user-key --project=climaticsystem)"
export ORION_USER_KEY
echo "[ok] secret loaded len=${#ORION_USER_KEY}"

python3 -m scripts.orion_ews.ingest doctor --ping --no-from-secret
python3 -m scripts.orion_ews.ingest smoke --days 3 --out-dir /tmp/orion_smoke \
  --bq-project climaticsystem \
  --gcs-prefix gs://efc-clima-data/obs/ews/ \
  --no-from-secret

# Best-effort IAM for Cloud Run
bash scripts/orion_ews/grant_secret_access.sh || true

echo "[done] smoke finished"
