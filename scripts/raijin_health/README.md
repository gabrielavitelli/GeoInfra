# Raijin Alerta — health check

Checklist diário das fontes do sino (EWS limiares + ML `raijin_alerts`) e frescor BQ.

```bash
pip install google-cloud-bigquery google-cloud-storage
python3 scripts/raijin_health/check.py --pretty \
  --gcs-out gs://efc-clima-data/monitor/health/raijin_health.json
```

Exit codes: `0` core OK (GFS + Orion/EWS + MERGE) · `2` core degradado.

## Estado conhecido (2026-11-19)

| Peça | Status |
|------|--------|
| Limiares EWS (sino) | OK — Orion horário |
| MERGE 6h → BQ | Recuperado; scheduler `efc-ingestion-merge-daily` 08:15 UTC |
| ML `raijin_alerts` | Parado desde 2026-06-15 — falta rebuild de features |
| GOES | ABI MCMIPC **staged** em `gs://…/data/goes_abi/2026-11-1{7,8,9}/` (G19); falta extract→score |

