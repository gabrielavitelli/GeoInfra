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

Sensores vêm de BigQuery (`climaticsystem.efc_clima.zentra_devices`). Sem `USE_BQ=1` + ADC → tabela vazia.

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
