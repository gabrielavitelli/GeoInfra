# Monitor EFC — preview local (igual ao prod `monitor-efc-prod`)

## One-liner (PC)

```bash
bash "${GI:-$HOME/gabriela/GeoInfra}/_monitor_deploy/scripts/bootstrap_local_monitor_prodlike.sh"
```

Overrides comuns:

```bash
export CS="$HOME/gabriela/ClimaticSystem"   # tree Django (manage.py)
export GI="$HOME/gabriela/GeoInfra"
# Windows Git Bash:
# export CS="/c/Users/Operador/Documents/Gabriela/ClimaticSystem"
# export GI="/c/Users/Operador/Documents/Gabriela/GeoInfra"
bash "$GI/_monitor_deploy/scripts/bootstrap_local_monitor_prodlike.sh"
```

O bootstrap: fetch `cursor/zentra-sensor-detail-9ef8` → **rsync completo** de `_monitor_deploy` (monitor_web + libs + scripts) no ClimaticSystem (com backup) → ADC check → `USE_BQ=1` + `runserver`.

Só overlay (sem runserver): `bash …/bootstrap_local_monitor_prodlike.sh --no-run`

---

## Qual tree usar (não misture)

| Tree | O que é | Serve para preview prod? |
|------|---------|--------------------------|
| **ClimaticSystem** (`manage.py` + `efc_site/` + `monitor_web/`) | Tree Django completo | **Sim**, depois do overlay |
| **GeoInfra** `_monitor_deploy/` | Overlay (Zentra / 3D / Sensores / layer strip) | **Não sozinho** |
| Streamlit (`app_monitor_efc.py`) | UI antiga | **Não** |

Prod Cloud Run = **Django** `monitor_web`, **não** Streamlit.

Branch: `cursor/zentra-sensor-detail-9ef8` (inclui pluvio + map 3D + sensor detail).

Sensores vêm de BigQuery (`climaticsystem.efc_clima.zentra_devices` + `clima_series`).
Sem ADC / BQ falha → **fallback GCS** (mesmo catálogo do ingest):

| Env | Default |
|-----|---------|
| `USE_BQ` | `1` (obrigatório no preview prod-like) |
| `GOOGLE_CLOUD_PROJECT` | `climaticsystem` |
| `BQ_DATASET` | `efc_clima` |
| `ZENTRA_DEVICES_GCS_URI` | `gs://efc-clima-data/obs/zentra/zentra_devices_latest.json` |
| `ZENTRA_LOCATIONS_GCS_URI` | `gs://efc-clima-data/obs/zentra/zentra_locations_latest.json` |
| `ZENTRA_SERIES_GCS_URI` | `gs://efc-clima-data/obs/zentra/zentra_clima_series_latest.ndjson` |
| `ZENTRA_READINGS_GCS_URI` | `gs://efc-clima-data/obs/zentra/zentra_readings_latest.ndjson` |
| `ZENTRA_SKIP_GCS_FALLBACK` | unset (set `1` to force BQ-only) |

ADC: `gcloud auth application-default login` + `gcloud auth application-default set-quota-project climaticsystem`.

### Verificar ADC + contar Zentra (mesma função da view)

```bash
export CS="${CS:-$HOME/gabriela/ClimaticSystem}"
export GI="${GI:-$HOME/gabriela/GeoInfra}"
export PATH="$HOME/google-cloud-sdk/bin:$PATH"   # se preciso
gcloud config set project climaticsystem
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
gcloud auth application-default set-quota-project climaticsystem

# Overlay atualizado (inclui GCS fallback)
bash "$GI/_monitor_deploy/scripts/sync_zentra_overlay_into_climaticsystem.sh" "$CS"

cd "$CS" && source .venv/bin/activate
export USE_BQ=1 GOOGLE_CLOUD_PROJECT=climaticsystem BQ_DATASET=efc_clima
export DJANGO_SETTINGS_MODULE=efc_site.settings
python - <<'PY'
from monitor_web.services.zentra_sensors import build_zentra_sensor_rows
rows = build_zentra_sensor_rows()
print(len(rows), "Zentra")
print([r["name"] for r in rows])
PY
# Esperado: 8 Zentra
```

Se BQ falhar mas ADC ler GCS, o print ainda deve mostrar **8**. Sem ADC nem para GCS → `0` + "Nenhum sensor Zentra ativo."

---

## Overlay manual (rsync completo)

```bash
bash "$GI/_monitor_deploy/scripts/sync_zentra_overlay_into_climaticsystem.sh" "$CS"
```

Isso faz backup em `$CS/.overlay_backups/pre_overlay_*` e sincroniza **todo** `monitor_web/` + `lib_efc.py` + `monitor_i18n.py` + scripts/docs — não uma lista curta de arquivos.

---

## URLs / smoke

- Visão geral: http://127.0.0.1:8000/portal/
- Sensores: http://127.0.0.1:8000/portal/sensores/ → **8 Zentra** + botão **Série**
- Layer strip no mapa: botões **Zentra** + **3D** (MapLibre lazy)

```bash
curl -s http://127.0.0.1:8000/portal/sensores/ | grep -o '[0-9]* Zentra'
curl -s "http://127.0.0.1:8000/portal/fragments/home/map/?date=$(date -u +%F)&accum=720&km=379" \
  | grep -E 'data-basemap="3d"|data-overlay="zentra"'
curl -s "http://127.0.0.1:8000/portal/fragments/home/map-embed/?date=$(date -u +%F)&accum=720&km=379" \
  | grep -o 'z6-[0-9]*' | sort -u
```

Login (se auth ligado): `gestor.demo` / `Demo2026!`

---

## Cloud agent

```bash
export HOME=/home/ubuntu
export PATH="/home/ubuntu/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
export CS=/tmp/zentra_monitor_work
export GI=/agent/repos/GeoInfra
bash "$GI/_monitor_deploy/scripts/bootstrap_local_monitor_prodlike.sh"
```

---

## Deploy prod

Só depois do smoke local com **8 sensores** e UI igual ao prod — e se o usuário pedir:

```bash
cd "$CS"
./gcp/deploy_monitor_cloudrun_prod.sh -p climaticsystem -r us-central1
```
