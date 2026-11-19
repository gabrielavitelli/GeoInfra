#!/usr/bin/env bash
# Bootstrap ÚNICO — monitor-efc local igual ao prod (Zentra, 3D MapLibre, layer strip, Sensores).
#
# No PC (Git Bash / WSL / macOS / Linux):
#   bash /caminho/para/GeoInfra/_monitor_deploy/scripts/bootstrap_local_monitor_prodlike.sh
#
# Paths (override se precisar):
#   GI  = pasta GeoInfra (default: ao lado deste script, ou $HOME/gabriela/GeoInfra)
#   CS  = pasta ClimaticSystem com manage.py (default: $HOME/gabriela/ClimaticSystem)
#
# Flags:
#   --no-run      só fetch + overlay + ADC check (não sobe runserver)
#   --skip-adc    não valida Application Default Credentials
#   --skip-fetch  não faz git fetch/checkout no GeoInfra
#   --port N      porta do runserver (default 8000)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NO_RUN=0
SKIP_ADC=0
SKIP_FETCH=0
PORT="${PORT:-8000}"

for arg in "$@"; do
  case "$arg" in
    --no-run) NO_RUN=1 ;;
    --skip-adc) SKIP_ADC=1 ;;
    --skip-fetch) SKIP_FETCH=1 ;;
    --port=*) PORT="${arg#--port=}" ;;
    --port)
      echo "Use --port=N" >&2
      exit 2
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
  esac
done

# --- resolve GeoInfra ---
if [[ -n "${GI:-}" ]]; then
  :
elif [[ -d "$SCRIPT_DIR/../../.git" && -d "$SCRIPT_DIR/../monitor_web" ]]; then
  GI="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [[ -d "${HOME}/gabriela/GeoInfra/.git" ]]; then
  GI="${HOME}/gabriela/GeoInfra"
elif [[ -d "${HOME}/GeoInfra/.git" ]]; then
  GI="${HOME}/GeoInfra"
else
  echo "ERRO: defina GI=/caminho/para/GeoInfra" >&2
  exit 1
fi
GI="$(cd "$GI" && pwd)"

# --- resolve ClimaticSystem ---
CS="${CS:-${HOME}/gabriela/ClimaticSystem}"
if [[ ! -f "$CS/manage.py" ]]; then
  # Fallbacks comuns (Windows paths via Git Bash / agent)
  for cand in \
    "${HOME}/gabriela/SummerSchool/seasonal_calibration_verification/EFC" \
    "/c/Users/Operador/Documents/Gabriela/ClimaticSystem" \
    "/tmp/zentra_monitor_work" \
    "/tmp/monitor_deploy_build"
  do
    if [[ -f "$cand/manage.py" && -d "$cand/efc_site" && -d "$cand/monitor_web" ]]; then
      CS="$cand"
      break
    fi
  done
fi
if [[ ! -f "$CS/manage.py" || ! -d "$CS/efc_site" || ! -d "$CS/monitor_web" ]]; then
  cat >&2 <<EOF
ERRO: ClimaticSystem (tree Django) não encontrado em:
  CS=$CS

Precisa de manage.py + efc_site/ + monitor_web/.
Exporte CS apontando para a pasta correta, por exemplo:
  export CS="\$HOME/gabriela/ClimaticSystem"
  # Windows Git Bash:
  export CS="/c/Users/Operador/Documents/Gabriela/ClimaticSystem"

GeoInfra sozinho (_monitor_deploy) NÃO roda o portal — é só o overlay.
EOF
  exit 1
fi
CS="$(cd "$CS" && pwd)"

BRANCH="${MONITOR_BRANCH:-cursor/zentra-sensor-detail-9ef8}"
# Branches extras só para garantir tip (overlay final = zentra-sensor-detail)
EXTRA_BRANCHES=(
  cursor/orion-pluvio-monitor-9ef8
  cursor/zentra-monitor-map-9ef8
)

echo "==> GeoInfra: $GI"
echo "==> ClimaticSystem: $CS"
echo "==> Branch overlay: $BRANCH"

# --- 1) fetch / checkout GeoInfra ---
if [[ "$SKIP_FETCH" -eq 0 ]]; then
  cd "$GI"
  if [[ -d .git ]]; then
    git fetch origin "$BRANCH" || git fetch origin
    for b in "${EXTRA_BRANCHES[@]}"; do
      git fetch origin "$b" 2>/dev/null || true
    done
    git checkout "$BRANCH"
    git pull --ff-only origin "$BRANCH" 2>/dev/null || git merge --ff-only "origin/$BRANCH" 2>/dev/null || true
  else
    echo "AVISO: $GI sem .git — usando tree local como está."
  fi
else
  echo "==> --skip-fetch: usando GeoInfra local"
fi

OVERLAY="$GI/_monitor_deploy"
SYNC="$OVERLAY/scripts/sync_zentra_overlay_into_climaticsystem.sh"
if [[ ! -x "$SYNC" ]]; then
  chmod +x "$SYNC" 2>/dev/null || true
fi
if [[ ! -f "$SYNC" ]]; then
  echo "ERRO: sync script ausente: $SYNC" >&2
  exit 1
fi

# --- 2) overlay completo (rsync + backup) ---
echo "==> Overlay rsync completo → ClimaticSystem"
bash "$SYNC" "$CS"

# --- 3) ADC / projeto ---
export CLOUDSDK_CORE_PROJECT="${CLOUDSDK_CORE_PROJECT:-climaticsystem}"
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-climaticsystem}"
export BQ_DATASET="${BQ_DATASET:-efc_clima}"
export USE_BQ="${USE_BQ:-1}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-efc_site.settings}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-1}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
export EFC_MONITOR_DISABLE_AUTH="${EFC_MONITOR_DISABLE_AUTH:-1}"
export MONITOR_STATIC_BUST="${MONITOR_STATIC_BUST:-20261119hyp3br3d1b}"

if [[ "$SKIP_ADC" -eq 0 ]]; then
  echo "==> Checando Application Default Credentials (BQ)"
  if command -v gcloud >/dev/null 2>&1; then
    gcloud config set project climaticsystem >/dev/null
    if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
      cat >&2 <<EOF
ERRO: ADC ausente/expirado. Rode uma vez:
  gcloud auth login gabrielavitelli@gmail.com
  gcloud auth application-default login
  gcloud auth application-default set-quota-project climaticsystem
EOF
      exit 1
    fi
    gcloud auth application-default set-quota-project climaticsystem >/dev/null 2>&1 || true
  else
    echo "AVISO: gcloud não está no PATH — pulando check ADC (USE_BQ=1 ainda requer credenciais)."
  fi
fi

# --- 4) runserver ---
cd "$CS"
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

python manage.py migrate --noinput

echo ""
echo "Portal:   http://127.0.0.1:${PORT}/portal/"
echo "Sensores: http://127.0.0.1:${PORT}/portal/sensores/   (esperado: 8 Zentra)"
echo "Smoke:    curl -s http://127.0.0.1:${PORT}/portal/sensores/ | grep -o '[0-9]* Zentra'"
echo "          curl -s 'http://127.0.0.1:${PORT}/portal/fragments/home/map/?date=$(date -u +%F)&accum=720&km=379' | grep -E 'data-basemap=\"3d\"|data-overlay=\"zentra\"'"
echo ""

if [[ "$NO_RUN" -eq 1 ]]; then
  echo "OK (--no-run). Para subir:"
  echo "  cd $CS && source .venv/bin/activate && ./scripts/run_django_monitor.sh"
  exit 0
fi

# Mata runserver antigo na mesma porta se for nosso tree (best-effort)
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
fi

exec python manage.py runserver "127.0.0.1:${PORT}"
