#!/usr/bin/env bash
# Build image once + create/update Cloud Run Job + 6h Scheduler.
# Does NOT schedule Cloud Build every 6h (cheap posture).
set -euo pipefail

PROJECT="${PROJECT:-climaticsystem}"
REGION="${REGION:-us-central1}"
IMAGE="${IMAGE:-gcr.io/climaticsystem/zentra-ingest}"
JOB="${JOB:-zentra-ingest}"
SCHEDULER="${SCHEDULER:-zentra-ingest-6h}"
SA="${SA:-894301304159-compute@developer.gserviceaccount.com}"
SECRET="${SECRET:-zentra-api-token}"
GCS_PREFIX="${GCS_PREFIX:-gs://efc-clima-data/obs/zentra/}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "==> Apply BQ DDL (zentra_devices)"
bq query --project_id="$PROJECT" --use_legacy_sql=false < "$ROOT/scripts/zentra/sql/bq_zentra_schema.sql" \
  || echo "(DDL may already exist — continuing)"

echo "==> Grant secret accessor"
PROJECT_NUMBER=894301304159 SA="$SA" bash "$ROOT/scripts/zentra/grant_secret_access.sh"

echo "==> Build & push $IMAGE (one-shot Cloud Build)"
gcloud builds submit \
  --project="$PROJECT" \
  --config="$ROOT/scripts/zentra/cloudbuild.yaml" \
  --substitutions=_IMAGE="$IMAGE" \
  "$ROOT"

echo "==> Create/update Cloud Run Job $JOB"
if gcloud run jobs describe "$JOB" --project="$PROJECT" --region="$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" \
    --project="$PROJECT" \
    --region="$REGION" \
    --image="$IMAGE" \
    --command=python \
    --args="-m,scripts.zentra.ingest,run,--skip-stale,--bq-project,$PROJECT,--bq-dataset,efc_clima,--gcs-prefix,$GCS_PREFIX,--no-from-secret" \
    --set-env-vars="GCP_PROJECT=$PROJECT,BQ_DATASET=efc_clima,GCS_PREFIX=$GCS_PREFIX" \
    --update-secrets="ZENTRA_API_TOKEN=${SECRET}:latest" \
    --service-account="$SA" \
    --memory=512Mi \
    --cpu=1 \
    --task-timeout=900 \
    --max-retries=1 \
    --quiet
else
  gcloud run jobs create "$JOB" \
    --project="$PROJECT" \
    --region="$REGION" \
    --image="$IMAGE" \
    --command=python \
    --args="-m,scripts.zentra.ingest,run,--skip-stale,--bq-project,$PROJECT,--bq-dataset,efc_clima,--gcs-prefix,$GCS_PREFIX,--no-from-secret" \
    --set-env-vars="GCP_PROJECT=$PROJECT,BQ_DATASET=efc_clima,GCS_PREFIX=$GCS_PREFIX" \
    --set-secrets="ZENTRA_API_TOKEN=${SECRET}:latest" \
    --service-account="$SA" \
    --memory=512Mi \
    --cpu=1 \
    --task-timeout=900 \
    --max-retries=1 \
    --quiet
fi

echo "==> Scheduler $SCHEDULER → Jobs :run every 6h"
URI="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "$SCHEDULER" --project="$PROJECT" --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER" \
    --project="$PROJECT" \
    --location="$REGION" \
    --schedule="0 */6 * * *" \
    --time-zone="Etc/UTC" \
    --uri="$URI" \
    --http-method=POST \
    --oauth-service-account-email="$SA" \
    --message-body="{}" \
    --headers="Content-Type=application/json" \
    --quiet
else
  gcloud scheduler jobs create http "$SCHEDULER" \
    --project="$PROJECT" \
    --location="$REGION" \
    --schedule="0 */6 * * *" \
    --time-zone="Etc/UTC" \
    --uri="$URI" \
    --http-method=POST \
    --oauth-service-account-email="$SA" \
    --message-body="{}" \
    --headers="Content-Type=application/json" \
    --quiet
fi

echo "==> Smoke one execution"
gcloud run jobs execute "$JOB" --project="$PROJECT" --region="$REGION" --wait

echo "Done."
echo "  Job:       $JOB"
echo "  Schedule:  0 */6 * * * (Etc/UTC) via $SCHEDULER"
echo "  GCS:       $GCS_PREFIX"
echo "  BQ:        ${PROJECT}.efc_clima.clima_series source=zentra"
