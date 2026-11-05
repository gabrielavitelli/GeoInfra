#!/usr/bin/env bash
# Grant Secret Manager access on orion-user-key to Cloud Run / Cloud Build SAs.
# Usage:
#   ./scripts/orion_ews/grant_secret_access.sh
#   SA=my-sa@climaticsystem.iam.gserviceaccount.com ./scripts/orion_ews/grant_secret_access.sh
set -euo pipefail

PROJECT="${PROJECT:-climaticsystem}"
SECRET="${SECRET:-orion-user-key}"
# Default compute SA + common Cloud Run runtime SA patterns for this project.
DEFAULT_SAS=(
  "${PROJECT_NUMBER:-894301304159}-compute@developer.gserviceaccount.com"
  "climaticsystem@appspot.gserviceaccount.com"
)

if [[ -n "${SA:-}" ]]; then
  SAS=("$SA")
else
  # Discover Cloud Run service account for monitor-efc-prod when possible.
  DISCOVERED="$(gcloud run services describe monitor-efc-prod \
    --project="$PROJECT" --region=us-central1 \
    --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"
  SAS=("${DEFAULT_SAS[@]}")
  if [[ -n "$DISCOVERED" ]]; then
    SAS+=("$DISCOVERED")
  fi
fi

# Unique
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
