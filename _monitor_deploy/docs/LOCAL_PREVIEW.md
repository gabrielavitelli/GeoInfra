# Monitor EFC — preview local (antes do deploy)

O tree completo do Django fica em um workdir local (não só `_monitor_deploy/`).
No cloud agent já há cópia em `/tmp/zentra_monitor_work` (sync a partir do deploy
de `monitor-efc-prod` + patches desta branch).

## Comandos (copy-paste)

```bash
export PATH="/home/ubuntu/google-cloud-sdk-install/google-cloud-sdk/bin:$PATH"
export CLOUDSDK_CORE_PROJECT=climaticsystem
export GOOGLE_CLOUD_PROJECT=climaticsystem
export BQ_DATASET=efc_clima
export USE_BQ=1
export DJANGO_SETTINGS_MODULE=efc_site.settings
export DJANGO_DEBUG=1
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
# Bypass login local (settings lê EFC_MONITOR_DISABLE_AUTH, não MONITOR_AUTH_DISABLED)
export EFC_MONITOR_DISABLE_AUTH=1
export MONITOR_STATIC_BUST=20261117sensoresRestore
# ADC: gcloud auth application-default login  (gabrielavitelli@gmail.com / climaticsystem)

cd /tmp/zentra_monitor_work   # tree Django completo (não só _monitor_deploy/)

python3 -m venv .venv
source .venv/bin/activate
pip install -q -U pip
pip install -q -r gcp/requirements_monitor_cloudrun.txt
python manage.py migrate --noinput
python manage.py runserver 127.0.0.1:8000
```

Atalho equivalente (depois de `export EFC_MONITOR_DISABLE_AUTH=1`):  
`./scripts/run_django_monitor.sh`

## URLs

- Visão geral (mapa): http://127.0.0.1:8000/portal/
- Sensores (tabela prod + Série): http://127.0.0.1:8000/portal/sensores/
- Sensores interno: http://127.0.0.1:8000/sensores/
- Login (se auth ligado): http://127.0.0.1:8000/portal/login/ → `gestor.demo` / `Demo2026!`

## Smoke rápido

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health/
curl -s "http://127.0.0.1:8000/portal/api/home/sensor-series/?source=zentra&location_id=zentra_z6_35938&variable=soil_vwc&days=7"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/portal/sensores/
```

## Sync patches → workdir

```bash
# Depois de editar em GeoInfra/_monitor_deploy:
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/services/*.py \
  /tmp/zentra_monitor_work/monitor_web/services/
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/views.py \
  /agent/repos/GeoInfra/_monitor_deploy/monitor_web/urls.py \
  /agent/repos/GeoInfra/_monitor_deploy/monitor_web/portal_*.py \
  /agent/repos/GeoInfra/_monitor_deploy/monitor_web/permissions.py \
  /agent/repos/GeoInfra/_monitor_deploy/monitor_web/context_processors.py \
  /tmp/zentra_monitor_work/monitor_web/
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/static/monitor/js/monitor.js \
  /tmp/zentra_monitor_work/monitor_web/static/monitor/js/
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/static/monitor/css/monitor.css \
  /tmp/zentra_monitor_work/monitor_web/static/monitor/css/
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/templates/monitor/sensores.html \
  /tmp/zentra_monitor_work/monitor_web/templates/monitor/
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/templates/monitor/sensores.html \
  /tmp/zentra_monitor_work/monitor_web/templates/monitor/portal/
cp -a /agent/repos/GeoInfra/_monitor_deploy/monitor_web/templates/monitor/components \
  /tmp/zentra_monitor_work/monitor_web/templates/monitor/
# runserver com --reload já recarrega
```

## Deploy prod (só depois do smoke)

```bash
cd /tmp/zentra_monitor_work
./gcp/deploy_monitor_cloudrun_prod.sh -p climaticsystem -r us-central1
```
