# Orion Data (EWS) → climaticsystem / monitor EFC

API: [Orion Data v1.3](https://docs.oriondata.io/api/orion-data/v1-3/)  
Auth: **User Key** (Secret Manager `orion-user-key` @ `climaticsystem`) → `/token` → Bearer JWT. Login/senha = só portal.

## Endpoints

| Call | Uso |
|---|---|
| `GET /token?apiKey=` | JWT |
| `GET /userDevices` | Catálogo (nome, IMEI, battery%, status, lat/lon, sensores) |
| `GET /sensorData` | Séries (`readingDate`, `sensorValue`) |
| `GET /validatedSensorData` | Séries + qualidade |

## Destino EFC

`clima_series` (colunas reais): `date`, `location_id`, `source`, `ingested_at`, `variable`, `value`

- `source`: `orion` (ou `ews`)
- `location_id`: `ews_EWS6536` (cf. `estacao_A044`)
- Catálogo: tabela `orion_devices` + opcional MERGE em `locations`
- GCS: `gs://efc-clima-data/obs/ews/orion_*_latest.*`

## Comandos

```bash
# Lê secret via gcloud (não imprime o valor) + ping API
python3 -m scripts.orion_ews.ingest doctor --ping

# Smoke completo (3 dias). Com BQ/GCS:
python3 -m scripts.orion_ews.ingest smoke --days 3 \
  --bq-project climaticsystem \
  --gcs-prefix gs://efc-clima-data/obs/ews/

# IAM para Cloud Run / compute SA ler o secret
bash scripts/orion_ews/grant_secret_access.sh
```

Fixture (sem rede Orion):

```bash
python3 -m scripts.orion_ews.ingest catalog \
  --fixture scripts/orion_ews/fixtures/user_devices_sample.json
```

## Refresh

1. Secret já em `orion-user-key` v1.  
2. Job: Cloud Build `cloudbuild-orion-ingest.yaml` ou `ingest smoke` no Scheduler.  
3. Monitor mapa: incluir `source IN ('orion','ews')` / `location_id LIKE 'ews_%'` no catálogo.
