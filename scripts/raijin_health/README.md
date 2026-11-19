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

## Estado conhecido (2026-11-19)

| Peça | Status |
|------|--------|
| Limiares EWS (sino) | OK — Orion horário |
| MERGE 6h → BQ | Recuperado; scheduler `efc-ingestion-merge-daily` 08:15 UTC |
| ML `raijin_alerts` | Religado (`cascade_v2_ops`) — max_day 19/11; 100 TP / 71 FP em 17/11 |
| GOES | ABI G19 staged `gs://…/data/goes_abi/2026-11-1{7,8,9}/` |

