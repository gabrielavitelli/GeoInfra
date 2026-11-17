# Monitor EFC — preview local (igual ao prod `monitor-efc-prod`)

## Qual tree usar (não misture)

| Tree | O que é | Serve para preview prod? |
|------|---------|--------------------------|
| **ClimaticSystem** (pasta com `manage.py` + `efc_site/` + `monitor_web/`) | Tree Django completo do monitor | **Sim**, depois de overlay da branch GeoInfra |
| **GeoInfra** `_monitor_deploy/` | Overlay (templates/JS/CSS/services Zentra) | **Não sozinho** — não tem `manage.py` |
| Streamlit (`app_monitor_efc.py` / `streamlit run …`) | UI antiga | **Não** — visual totalmente diferente do Cloud Run |

Prod Cloud Run = **Django** `monitor_web` (portal stitch + aba Sensores Zentra), **não** Streamlit.

Branch certa: `cursor/zentra-sensor-detail-9ef8` (PR #13) em `gabrielavitelli/GeoInfra`.

Sensores vêm de BigQuery (`climaticsystem.efc_clima.zentra_devices` + `clima_series` com `source='zentra'`). Sem `USE_BQ=1` + ADC, a tabela fica **vazia**.

---

## No PC (ClimaticSystem) — copy-paste

Ajuste `CS` se a pasta for outra (ex. WSL: `~/gabriela/ClimaticSystem`).

```bash
# 0) paths
export CS="${CS:-$HOME/gabriela/ClimaticSystem}"
export GI="${GI:-$HOME/gabriela/GeoInfra}"   # ou onde clonou GeoInfra

# 1) GeoInfra na branch do monitor + Zentra
cd "$GI"
git fetch origin
git checkout cursor/zentra-sensor-detail-9ef8
git pull origin cursor/zentra-sensor-detail-9ef8

# 2) ClimaticSystem = tree Django (não use só GeoInfra)
cd "$CS"
test -f manage.py && test -d efc_site && test -d monitor_web || {
  echo "ERRO: $CS não é o tree Django do monitor (falta manage.py/efc_site/monitor_web)"
  exit 1
}

# 3) Overlay GeoInfra → ClimaticSystem
bash "$GI/_monitor_deploy/scripts/sync_zentra_overlay_into_climaticsystem.sh" "$CS"

# 4) ADC + projeto (uma vez / quando expirar)
gcloud auth login gabrielavitelli@gmail.com
gcloud auth application-default login
gcloud config set project climaticsystem
gcloud auth application-default set-quota-project climaticsystem

# 5) env obrigatório para UI + sensores BQ
export CLOUDSDK_CORE_PROJECT=climaticsystem
export GOOGLE_CLOUD_PROJECT=climaticsystem
export BQ_DATASET=efc_clima
export USE_BQ=1
export DJANGO_SETTINGS_MODULE=efc_site.settings
export DJANGO_DEBUG=1
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
# settings só lê EFC_MONITOR_DISABLE_AUTH (MONITOR_AUTH_DISABLED sozinho NÃO basta)
export EFC_MONITOR_DISABLE_AUTH=1
export MONITOR_STATIC_BUST=20261117sensoresRestore

# 6) venv + run
cd "$CS"
python3 -m venv .venv
source .venv/bin/activate
pip install -q -U pip
pip install -q -r gcp/requirements_monitor_cloudrun.txt
python manage.py migrate --noinput
python manage.py runserver 127.0.0.1:8000
```

Atalho (depois dos `export` acima):

```bash
cd "$CS"
# o script do overlay já corrige auth/BQ se existir em GeoInfra:
bash "$GI/_monitor_deploy/scripts/run_django_monitor.sh"
# ou, se preferir o da pasta ClimaticSystem (após sync):
./scripts/run_django_monitor.sh
```

### Windows (Git Bash / WSL)

Mesmos comandos. Exemplos de path:

```bash
export CS="/c/Users/Operador/Documents/Gabriela/ClimaticSystem"
# ou SummerSchool/EFC se for aí que está manage.py:
# export CS="/c/Users/Operador/Documents/Gabriela/SummerSchool/seasonal_calibration_verification/EFC"
export GI="/c/Users/Operador/Documents/Gabriela/GeoInfra"
```

**Não** rode `streamlit run app_monitor_efc.py` para comparar com
https://monitor-efc-prod-zws527es5a-uc.a.run.app/

---

## Cloud agent (já provisionado)

```bash
export PATH="/home/ubuntu/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
export CLOUDSDK_CORE_PROJECT=climaticsystem
export GOOGLE_CLOUD_PROJECT=climaticsystem
export BQ_DATASET=efc_clima
export USE_BQ=1
export DJANGO_SETTINGS_MODULE=efc_site.settings
export DJANGO_DEBUG=1
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
export EFC_MONITOR_DISABLE_AUTH=1
export MONITOR_STATIC_BUST=20261117sensoresRestore

cd /tmp/zentra_monitor_work   # tree Django completo (não só GeoInfra/_monitor_deploy)

source .venv/bin/activate 2>/dev/null || { python3 -m venv .venv && source .venv/bin/activate; }
pip install -q -U pip
pip install -q -r gcp/requirements_monitor_cloudrun.txt
python manage.py migrate --noinput
python manage.py runserver 127.0.0.1:8000
```

---

## URLs

- Visão geral (mapa): http://127.0.0.1:8000/portal/
- Sensores (tabela prod + botão Série): http://127.0.0.1:8000/portal/sensores/
- Sensores interno: http://127.0.0.1:8000/sensores/
- Login (se auth ligado): http://127.0.0.1:8000/portal/login/ → `gestor.demo` / `Demo2026!`

Esperado em Sensores: **8 Zentra** ativos (`z6-35936` … `z6-35950`).

---

## Smoke

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/portal/sensores/
curl -s "http://127.0.0.1:8000/portal/api/home/sensor-series/?source=zentra&location_id=zentra_z6_35938&variable=soil_vwc&days=7"
# BQ (opcional):
# bq query --use_legacy_sql=false \
#  'SELECT status, COUNT(*) FROM `climaticsystem.efc_clima.zentra_devices` GROUP BY 1'
```

Se `/portal/sensores/` abrir mas `0 Zentra` / tabela vazia → ADC ou `USE_BQ` faltando (não é “branch errada” sozinho).

---

## Sync manual (se não usar o script)

```bash
CS="${CS:-$HOME/gabriela/ClimaticSystem}"
GI="${GI:-$HOME/gabriela/GeoInfra}"
SRC="$GI/_monitor_deploy"

cp -a "$SRC/monitor_web/services/"*.py "$CS/monitor_web/services/"
cp -a "$SRC/monitor_web/views.py" "$SRC/monitor_web/urls.py" \
  "$SRC/monitor_web/portal_urls.py" "$SRC/monitor_web/portal_views.py" \
  "$SRC/monitor_web/permissions.py" "$SRC/monitor_web/context_processors.py" \
  "$CS/monitor_web/"
cp -a "$SRC/monitor_web/static/monitor/js/monitor.js" "$CS/monitor_web/static/monitor/js/"
cp -a "$SRC/monitor_web/static/monitor/css/monitor.css" "$CS/monitor_web/static/monitor/css/"
cp -a "$SRC/monitor_web/templates/monitor/sensores.html" "$CS/monitor_web/templates/monitor/"
mkdir -p "$CS/monitor_web/templates/monitor/portal"
cp -a "$SRC/monitor_web/templates/monitor/sensores.html" "$CS/monitor_web/templates/monitor/portal/"
cp -a "$SRC/monitor_web/templates/monitor/components" "$CS/monitor_web/templates/monitor/"
cp -a "$SRC/scripts/run_django_monitor.sh" "$CS/scripts/" 2>/dev/null || true
```

---

## Deploy prod

Só depois do smoke local com **8 sensores** e layout igual ao prod:

```bash
cd "$CS"   # ou /tmp/zentra_monitor_work no agent
./gcp/deploy_monitor_cloudrun_prod.sh -p climaticsystem -r us-central1
```
