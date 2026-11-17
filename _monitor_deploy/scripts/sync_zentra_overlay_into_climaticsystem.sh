#!/usr/bin/env bash
# Overlay GeoInfra/_monitor_deploy → ClimaticSystem (Django tree com manage.py).
# Uso:
#   bash _monitor_deploy/scripts/sync_zentra_overlay_into_climaticsystem.sh /path/to/ClimaticSystem
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"   # _monitor_deploy
DEST="${1:-}"

if [[ -z "$DEST" ]]; then
  echo "Uso: $0 /caminho/para/ClimaticSystem" >&2
  exit 2
fi
DEST="$(cd "$DEST" && pwd)"

if [[ ! -f "$DEST/manage.py" || ! -d "$DEST/monitor_web" || ! -d "$DEST/efc_site" ]]; then
  echo "ERRO: destino não parece o tree Django do monitor: $DEST" >&2
  echo "Precisa ter manage.py, efc_site/, monitor_web/." >&2
  exit 1
fi
if [[ ! -d "$SRC/monitor_web" ]]; then
  echo "ERRO: overlay incompleto em $SRC" >&2
  exit 1
fi

echo "Overlay: $SRC → $DEST"

mkdir -p "$DEST/monitor_web/services" \
  "$DEST/monitor_web/static/monitor/js" \
  "$DEST/monitor_web/static/monitor/css" \
  "$DEST/monitor_web/templates/monitor/portal" \
  "$DEST/scripts" \
  "$DEST/docs"

# Services adicionados/alterados nesta branch (não apaga os demais do ClimaticSystem)
shopt -s nullglob
for f in "$SRC/monitor_web/services/"*.py; do
  cp -a "$f" "$DEST/monitor_web/services/"
done
shopt -u nullglob

cp -a "$SRC/monitor_web/views.py" \
  "$SRC/monitor_web/urls.py" \
  "$SRC/monitor_web/portal_urls.py" \
  "$SRC/monitor_web/portal_views.py" \
  "$SRC/monitor_web/permissions.py" \
  "$SRC/monitor_web/context_processors.py" \
  "$DEST/monitor_web/"

cp -a "$SRC/monitor_web/static/monitor/js/monitor.js" "$DEST/monitor_web/static/monitor/js/"
cp -a "$SRC/monitor_web/static/monitor/css/monitor.css" "$DEST/monitor_web/static/monitor/css/"

cp -a "$SRC/monitor_web/templates/monitor/sensores.html" "$DEST/monitor_web/templates/monitor/"
cp -a "$SRC/monitor_web/templates/monitor/sensores.html" "$DEST/monitor_web/templates/monitor/portal/"
if [[ -d "$SRC/monitor_web/templates/monitor/components" ]]; then
  cp -a "$SRC/monitor_web/templates/monitor/components" "$DEST/monitor_web/templates/monitor/"
fi

cp -a "$SRC/scripts/run_django_monitor.sh" "$DEST/scripts/"
chmod +x "$DEST/scripts/run_django_monitor.sh" \
  "$SRC/scripts/sync_zentra_overlay_into_climaticsystem.sh" 2>/dev/null || true
cp -a "$SRC/docs/LOCAL_PREVIEW.md" "$DEST/docs/" 2>/dev/null || true

echo "OK. Rode com USE_BQ=1 + EFC_MONITOR_DISABLE_AUTH=1 (ver docs/LOCAL_PREVIEW.md)."
echo "  cd $DEST && ./scripts/run_django_monitor.sh"
