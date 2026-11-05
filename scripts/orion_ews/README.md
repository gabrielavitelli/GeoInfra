# Orion Data (EWS) → climaticsystem / monitor EFC

Integração da API [Orion Data v1.3](https://docs.oriondata.io/api/orion-data/v1-3/) (plataforma EWS Monitoring) com o pipeline EFC (`climaticsystem` / `efc_clima`).

## Auth: User Key ≠ login/senha

| Credencial | Uso |
|---|---|
| **User Key / API key** (Account Settings em https://oriondata.io/) | Única credencial da API |
| Login + senha | Só o portal web — **não** entram em código, git nem API |

Fluxo oficial:

1. `GET https://api.oriondata.io/api/token?apiKey=<USER_KEY>` → JWT (~7 dias)
2. Demais calls com `Authorization: Bearer <jwt>`

Endpoints: `/userDevices`, `/sensorData`, `/validatedSensorData`.

**Segurança:** guarde a User Key em Secret Manager (`climaticsystem`) ou env `ORION_USER_KEY`. Nunca commit, nunca cole a senha no chat se a User Key bastar.

```bash
# GCP (projeto climaticsystem)
gcloud secrets create orion-user-key --project=climaticsystem \
  --replication-policy=automatic
printf '%s' "$ORION_USER_KEY" | gcloud secrets versions add orion-user-key \
  --project=climaticsystem --data-file=-
```

## O que a API devolve

Por device (ex.: **EWS6536 - Abrigo km 149**, IMEI `301434060465170`):

- Catálogo: `deviceId`, `deviceName`, `serialNumber` (IMEI), `reference`, `dateInstalled`, `status` (Online/Offline), `lastUpload` (UTC), `latitude`/`longitude`, `batteryPercentage`
- Canais raw (`sensors`): `sensorId`, `sensorType`, `uom`, `channelNumber`, `customName`, `scadaTag`
- Soft sensors (calibrados/derivados): `softSensorId`, `sensorType`, `uom`, …
- Leituras: `readingDate` (hora local do device, sem DST), `sensorValue` (+ qualidade em `/validatedSensorData`)

Variáveis típicas no EWS Switch MKII (dependem da instalação): Water Level, Rainfall, Battery/Supply Voltage, barômetro, temperatura, switch/digital. O mapeamento para `clima_series` está em `mapping.py`.

## Estado atual no EFC (antes desta integração)

| Artefato | Situação |
|---|---|
| Repo GeoInfra | Mapas EWS hardcoded (EWS6987, EWS6970, EWS6971…); **sem** cliente Orion |
| `gs://efc-clima-data/obs/ews/ews_timeseries_consolidated.csv` | Séries históricas via **xlsx** manuais ( Gabriela ), não API |
| BQ `climaticsystem.efc_clima.clima_series` | `source=ana` tem `nivel_m`/`vazao_m3s`; **sem** `source=orion`/`ews` |
| Monitor Cloud Build | `lib_efc.py` / `bq_load_clima.py` — fora deste repo |

## Pipeline proposto

```
ORION_USER_KEY (Secret Manager)
        │
        ▼
  /token → JWT
        │
        ├─► /userDevices  ──► orion_devices (+ MERGE locations)
        │                   └── orion_sensors
        │
        └─► /sensorData   ──► orion_readings_staging
                            └── APPEND clima_series (source='orion')
                                      variable ∈ {nivel_m, precip_mm, …}
        │
        ▼
  (opcional) refresh camada mapa monitor / gs://efc-clima-data/obs/ews/
```

DDL: [`sql/bq_orion_schema.sql`](sql/bq_orion_schema.sql).

## Uso local (skeleton)

```bash
# Sem chave — fixture
python3 -m scripts.orion_ews.ingest catalog \
  --fixture scripts/orion_ews/fixtures/user_devices_sample.json \
  --out /tmp/orion_devices.json

python3 -m scripts.orion_ews.ingest readings \
  --fixture-devices scripts/orion_ews/fixtures/user_devices_sample.json \
  --fixture-readings scripts/orion_ews/fixtures/sensor_data_sample.json \
  --out /tmp/orion_readings.ndjson

# Com User Key (NÃO passe a chave em argv)
export ORION_USER_KEY='…'   # cole só a User Key (não a senha)
python3 -m scripts.orion_ews.ingest doctor --ping
python3 -m scripts.orion_ews.ingest catalog --out /tmp/orion_devices.json
python3 -m scripts.orion_ews.ingest readings --days 7 --out /tmp/orion_readings.ndjson

# Load BQ (precisa gcloud ADC + google-cloud-bigquery)
python3 -m scripts.orion_ews.ingest readings --days 3 \
  --bq-project climaticsystem --bq-dataset efc_clima
```

## Próximos passos (bloqueados na User Key)

1. Usuário cola **só a User Key** no chat (ou cria o secret `orion-user-key`).
2. Smoke: `doctor --ping` + `catalog` (confirmar EWS6536 e demais abrigos).
3. Ajustar `mapping.py` aos `sensorType` reais do tenant.
4. Aplicar DDL no BQ + job Cloud Scheduler / Cloud Build no monitor.
5. Ligar camada mapa a `source=orion` (além dos xlsx legacy em `obs/ews/`).
