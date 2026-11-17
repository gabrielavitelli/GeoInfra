#!/usr/bin/env bash
# Grant Secret Manager access on zentra-api-token to Cloud Run Job SA.
# Usage:
#   ./scripts/zentra/grant_secret_access.sh
#   SA=my-sa@climaticsystem.iam.gserviceaccount.com ./scripts/zentra/grant_secret_access.sh
set -euo pipefail

PROJECT="${PROJECT:-climaticsystem}"
SECRET="${SECRET:-zentra-api-token}"
DEFAULT_SAS=(
  "${PROJECT_NUMBER:-894301304159}-compute@developer.gserviceaccount.com"
  "climaticsystem@appspot.gserviceaccount.com"
)

if [[ -n "${SA:-}" ]]; then
  SAS=("$SA")
else
  SAS=("${DEFAULT_SAS[@]}")
  DISCOVERED="$(gcloud run jobs describe zentra-ingest \
    --project="$PROJECT" --region=us-central1 \
    --format='value(spec.template.spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
  if [[ -n "$DISCOVERED" ]]; then
    SAS+=("$DISCOVERED")
  fi
fi

mapfile -t SAS < <(printf '%s\n' "${SAS[@]}" | awk 'NF && !seen[$0]++')

echo "Project=$PROJECT secret=$SECRET"
for member in "${SAS[@]}"; do
  echo "→ roles/secretmanager.secretAccessor for serviceAccount:${member}"
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --project="$PROJECT" \
    --member="serviceAccount:${member}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet
done

echo "Done. Verify:"
echo "  gcloud secrets get-iam-policy $SECRET --project=$PROJECT"
