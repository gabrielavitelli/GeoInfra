# Orion Data (EWS) → climaticsystem / monitor EFC

API: [Orion Data v1.3](https://docs.oriondata.io/api/orion-data/v1-3/)  
Auth: **User Key** (Secret Manager `orion-user-key`) → JWT. Login/senha = só portal.

## Destino (horário)

`clima_series`: `source=orion`, `location_id=ews_*`, `variable=precip_mm`,  
`date` (DATE) + `valid_time` (TIMESTAMP horário = soma dos tips 15‑min).  
Também: `orion_devices` + `locations` (`source=orion`).

## Produção barata — Cloud Run Job 24×/dia

| Peça | Nome |
|------|------|
| Imagem | `gcr.io/climaticsystem/orion-ews-ingest` |
| Job | `orion-ews-precip` (1 vCPU, 512 Mi, ~1–2 min/run) |
| Scheduler | `orion-ews-precip-hourly` — `15 * * * *` America/Sao_Paulo |

**Por que barato:** deps já na imagem (sem `pip` / Cloud Build a cada hora).  
Estimativa ~**US$1–3/mês** em 24×/dia (vs ~US$5 com Cloud Build + pip).

```bash
# Rebuild imagem + redeploy job
gcloud builds submit --config=scripts/orion_ews/cloudbuild-orion-job-image.yaml \
  --project=climaticsystem scripts/orion_ews

# Rodar na mão
gcloud run jobs execute orion-ews-precip --region=us-central1 --wait
```

## Local / manual

```bash
python3 -m scripts.orion_ews.ingest doctor --ping
python3 -m scripts.orion_ews.ingest readings --precip-only --days 1 \
  --bq-project climaticsystem \
  --gcs-prefix gs://efc-clima-data/obs/ews/
# Só recarregar GCS → BQ (sem API Orion):
python3 -m scripts.orion_ews.load_gcs_to_bq_hourly
```

## Frequência

| Freq | Custo relativo | Quando |
|------|----------------|--------|
| **24×/dia** (atual) | baixo (Run Job) | painel quase ao vivo |
| 8×/dia | ~⅓ | apertar orçamento |
| 1×/dia | mínimo | só histórico |

Não use lookback de 7 dias no job horário.
