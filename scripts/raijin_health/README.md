# Raijin Alerta — health + score diário

Checklist das fontes do sino (EWS limiares + ML `raijin_alerts`) e job de score operacional.

```bash
pip install -r scripts/raijin_health/requirements.txt
python3 scripts/raijin_health/check.py --pretty \
  --gcs-out gs://efc-clima-data/monitor/health/raijin_health.json
python3 scripts/raijin_health/score_status.py --pretty
```

Exit codes (`check.py`): `0` core OK (GFS + Orion/EWS + MERGE) · `2` core degradado.

## Score operacional

```bash
pip install -r scripts/raijin_health/requirements-score.txt
# Catch-up manual
python3 scripts/raijin_health/operational_score.py \
  --day-start 2026-11-17 --day-end 2026-11-19 --load-bq
# Ou job completo (GOES NOAA → GCS → score → BQ)
python3 scripts/raijin_health/daily_job.py
```

Deploy Cloud Run:

```bash
gcloud builds submit --config=scripts/raijin_health/cloudbuild.yaml \
  --project=climaticsystem scripts/raijin_health
gcloud run jobs execute raijin-score --region=us-central1 --wait
```

## Campeão operacional (deploy)

Treino/export + upload GCS (features só as do score ops; limiar holdout FAR≤0.25):

```bash
python3 scripts/raijin_health/train_export_champion.py \
  --hist-goes /path/to/features_goes_hist_202410_202512.csv \
  --out-joblib /tmp/cascade_v2_model_champion.joblib \
  --balance-per-class 2000 --far-max 0.25 --upload-gcs
```

Job diário: Cloud Scheduler `raijin-score-daily` (`0 9 * * *` UTC) → Cloud Run Job `raijin-score`
(GOES NOAA → GCS → SVM+gate → `efc_clima.raijin_alerts`).

## Estado conhecido (2026-11-19)

| Peça | Status |
|------|--------|
| Limiares EWS (sino) | OK — Orion horário |
| MERGE 6h → BQ | Recuperado; scheduler `efc-ingestion-merge-daily` 08:15 UTC |
| ML `raijin_alerts` | Campeão VIF+hist+balance no GCS; holdout POD≈0.60 FAR≈0.18; lag 0 |
| Scheduler score | `raijin-score-daily` ENABLED `0 9 * * *` UTC |
| GOES | ABI G19 staged `gs://…/data/goes_abi/` (job diário) |

