#!/usr/bin/env bash
# Monitor EFC — Django local (igual Cloud Run / monitor-efc-prod)
#
# Depois do overlay, este arquivo fica em ClimaticSystem/scripts/.
# Em GeoInfra (_monitor_deploy/scripts/) ele NÃO encontra manage.py —
# use sync_zentra_overlay_into_climaticsystem.sh primeiro, ou:
#   MONITOR_ROOT=/path/to/ClimaticSystem ./run_django_monitor.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -n "${MONITOR_ROOT:-}" && -f "${MONITOR_ROOT}/manage.py" ]]; then
  ROOT="$(cd "$MONITOR_ROOT" && pwd)"
elif [[ -f "$SCRIPT_DIR/../manage.py" ]]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [[ -f "$SCRIPT_DIR/../../manage.py" ]]; then
  ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
else
  echo "ERRO: manage.py não encontrado." >&2
  echo "Este script precisa rodar no tree Django (ClimaticSystem / zentra_monitor_work)," >&2
  echo "não em GeoInfra/_monitor_deploy sozinho." >&2
  echo "  bash _monitor_deploy/scripts/sync_zentra_overlay_into_climaticsystem.sh \$CS" >&2
  echo "  cd \$CS && ./scripts/run_django_monitor.sh" >&2
  exit 1
fi
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q -U pip
REQ=""
if [[ -f gcp/requirements_monitor_cloudrun.txt ]]; then
  REQ=gcp/requirements_monitor_cloudrun.txt
elif [[ -f requirements_monitor.txt ]]; then
  REQ=requirements_monitor.txt
fi
if [[ -n "$REQ" ]]; then
  pip install -q -r "$REQ"
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-efc_site.settings}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-1}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
export CLOUDSDK_CORE_PROJECT="${CLOUDSDK_CORE_PROJECT:-climaticsystem}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-climaticsystem}"
export BQ_DATASET="${BQ_DATASET:-efc_clima}"
export USE_BQ="${USE_BQ:-1}"
# settings.py lê EFC_MONITOR_DISABLE_AUTH (não MONITOR_AUTH_DISABLED)
export EFC_MONITOR_DISABLE_AUTH="${EFC_MONITOR_DISABLE_AUTH:-1}"
export MONITOR_STATIC_BUST="${MONITOR_STATIC_BUST:-20261119sitewin}"

PORT="${PORT:-8000}"
echo "Tree: $ROOT"
echo "USE_BQ=$USE_BQ  EFC_MONITOR_DISABLE_AUTH=$EFC_MONITOR_DISABLE_AUTH  project=$GOOGLE_CLOUD_PROJECT"
echo "Portal:   http://127.0.0.1:${PORT}/portal/"
echo "Sensores: http://127.0.0.1:${PORT}/portal/sensores/"
echo "Login (se auth ligado): gestor.demo / Demo2026!"
python manage.py migrate --noinput
exec python manage.py runserver "127.0.0.1:${PORT}"
