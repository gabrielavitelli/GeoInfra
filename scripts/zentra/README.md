# ZENTRA Cloud → climaticsystem / monitor EFC

API: [ZENTRA Cloud v5](https://api.zentracloud.io/openapi.json)  
Auth: **X-API-Key** (Secret Manager `zentra-api-token` @ `climaticsystem`, v6+).

## Endpoints

| Call | Uso |
|---|---|
| `GET /v5/devices` | Catálogo (nome, model, lat/lon, ports TEROS/ATMOS) |
| `GET /v5/devices/{id}/data?since_reading_id=N` | Incremental (barato) |
| `GET /v5/devices/{id}/data?window=24h` | Bootstrap sem cursor |

## Mapeamento

| Sensor / measurement | `variable` | family |
|---|---|---|
| ATMOS → Precipitation | `precip_mm` | meteo |
| TEROS 12 → Water Content | `soil_vwc` | geotech |
| TEROS 21 → Matric Potential | `water_potential_kpa` | geotech |
| Battery Percent / Signal | `battery_pct` / `signal_pct` | health |

- `source`: `zentra`
- `location_id`: `zentra_z6_35936`
- Catálogo BQ: `efc_clima.zentra_devices`
- GCS: `gs://efc-clima-data/obs/zentra/` (`zentra_devices_latest.json`, `zentra_cursors.json`, latest ndjson)

Cluster Oct (site ~-4.26,-46.53): `z6-35936/38/39/40/46/48` active; `47/51/52` stale/empty. Org also has `z6-35937` e Almeida `z6-35950`.

## Postura barata

1. **Cloud Run Job** com imagem bakeada (`gcr.io/climaticsystem/zentra-ingest`) — **não** Cloud Build a cada 6h.  
2. Cursors `since_reading_id` em GCS → poucas páginas por device.  
3. Skip rápido de empty/stale (`--skip-stale`).  
4. Scheduler `0 */6 * * *` (4×/dia), 512Mi / 1 vCPU.

## Comandos

```bash
# Ping API (lê secret via gcloud; não imprime o valor)
python3 -m scripts.zentra.ingest doctor --ping

# Smoke local → BQ + GCS
python3 -m scripts.zentra.ingest smoke --skip-stale \
  --bq-project climaticsystem \
  --gcs-prefix gs://efc-clima-data/obs/zentra/

# Deploy job + scheduler 6h + uma execução
bash scripts/zentra/deploy_job.sh
```

## Refresh (produção)

Job `zentra-ingest` · Scheduler `zentra-ingest-6h` · SA compute `…@developer.gserviceaccount.com` com `secretAccessor` em `zentra-api-token`.

Mapa EFC: follow-up (`source=zentra` / `location_id LIKE 'zentra_%'`).
