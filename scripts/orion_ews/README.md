# Orion Data (EWS) → climaticsystem / monitor EFC

API: [Orion Data v1.3](https://docs.oriondata.io/api/orion-data/v1-3/)  
Auth: **User Key** (Secret Manager `orion-user-key` @ `climaticsystem`) → `/token` → Bearer JWT. Login/senha = só portal.

## Destino EFC (horário, sem agregar só por dia)

`clima_series`:

| col | uso Orion |
|---|---|
| `date` | DATE do dia UTC |
| `valid_time` | TIMESTAMP na **hora** (soma dos tips 15‑min daquela hora) |
| `location_id` | `ews_EWS6536` |
| `source` | `orion` |
| `variable` | `precip_mm` |
| `value` | mm na hora |

Também: `orion_devices` (nomes) + `locations` (`source=orion`).

GCS: `gs://efc-clima-data/obs/ews/orion_*_latest.*`

## Custo / frequência

| Frequência | Lookback | Estimativa* | Quando usar |
|---|---|---|---|
| **24×/dia** (`15 * * * *`) | `_DAYS=1`, precip-only | **~$2–8/mês** | **Recomendado** (painel “quase ao vivo”) |
| 8×/dia (`0 */3 * * *`) | `_DAYS=1` | ~⅓ do acima | Se precisar apertar orçamento |
| 1×/dia | `_DAYS=2` | mínimo | Só histórico |

\*Cloud Build `E2_MEDIUM` ~2–5 min + BQ load de ~0,5–5k linhas/hora + API Orion (sem custo GCP). Dominante = minutos de build, não BigQuery.

**24×/dia é barato o bastante** para este volume (~21 pluviômetros). Não use lookback de 7 dias no horário.

## Comandos

```bash
# Ping
python3 -m scripts.orion_ews.ingest doctor --ping

# Precip-only 1 dia → GCS + BQ horário
python3 -m scripts.orion_ews.ingest readings --precip-only --days 1 \
  --bq-project climaticsystem \
  --gcs-prefix gs://efc-clima-data/obs/ews/

# Só recarregar NDJSON já no GCS (zero chamada Orion)
python3 -m scripts.orion_ews.load_gcs_to_bq_hourly
```

## Scheduler horário (24×)

```bash
gcloud builds submit --config=scripts/orion_ews/cloudbuild-orion-ingest.yaml \
  --project=climaticsystem --substitutions=_DAYS=1

gcloud scheduler jobs create http orion-ews-precip-hourly \
  --project=climaticsystem --location=us-central1 \
  --schedule='15 * * * *' --time-zone=America/Sao_Paulo \
  --uri='https://cloudbuild.googleapis.com/v1/projects/climaticsystem/builds' \
  --message-body-from-file=scripts/orion_ews/scheduler_build_body.json \
  --oauth-service-account-email=894301304159-compute@developer.gserviceaccount.com \
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform
```

IAM: compute SA precisa `roles/cloudbuild.builds.editor` (+ secret accessor no Cloud Build SA, já concedido).
