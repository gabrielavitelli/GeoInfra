#!/usr/bin/env bash
# Cloud Scheduler + Cloud Run Job: refresh boletim ~2m imagery every 10 days.
set -euo pipefail
PROJECT="${1:-climaticsystem}"
REGION="${2:-us-central1}"
JOB="efc-boletim-imagery-refresh"
SCHEDULER="efc-boletim-imagery-10d"
GCS="${EFC_BOLETIM_IMAGERY_GCS:-gs://efc-clima-data/monitor/boletim-imagery}"
IMAGE="${IMAGE:-gcr.io/${PROJECT}/monitor-efc-prod}"

export PATH="${PATH}:/home/ubuntu/google-cloud-sdk-install/google-cloud-sdk/bin"
export CLOUDSDK_PYTHON="${CLOUDSDK_PYTHON:-/usr/bin/python3}"

echo "Ensuring Cloud Run Job $JOB (image=$IMAGE)"
gcloud run jobs describe "$JOB" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud run jobs create "$JOB" \
    --image "$IMAGE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --tasks 1 \
    --max-retries 1 \
    --task-timeout 3600 \
    --memory 2Gi \
    --cpu 1 \
    --set-env-vars "EFC_BOLETIM_IMAGERY_GCS=${GCS},USE_BQ=1" \
    --command python \
    --args "scripts/refresh_boletim_km_imagery.py,--step,5"

gcloud run jobs update "$JOB" \
  --region "$REGION" \
  --project "$PROJECT" \
  --image "$IMAGE" \
  --set-env-vars "EFC_BOLETIM_IMAGERY_GCS=${GCS},USE_BQ=1" \
  --command python \
  --args "scripts/refresh_boletim_km_imagery.py,--step,5" \
  --quiet || true

gcloud scheduler jobs delete "$SCHEDULER" --location "$REGION" --project "$PROJECT" --quiet 2>/dev/null || true
# Every 10 days at 03:15 America/Sao_Paulo
gcloud scheduler jobs create http "$SCHEDULER" \
  --location "$REGION" \
  --project "$PROJECT" \
  --schedule="15 3 */10 * *" \
  --time-zone="America/Sao_Paulo" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" \
  --http-method=POST \
  --oauth-service-account-email="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --attempt-deadline=180s \
  --description="Refresh boletim ~2m km imagery every 10 days"

echo "OK: scheduler $SCHEDULER → job $JOB → $GCS"
