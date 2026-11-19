#!/usr/bin/env bash
# Overlay completo GeoInfra/_monitor_deploy → ClimaticSystem (tree Django com manage.py).
#
# Diferente da versão antiga (lista curta de arquivos): faz rsync de
#   monitor_web/  +  lib_efc.py  +  monitor_i18n.py  +  scripts/  +  docs/
# com backup timestamped do destino antes do overlay destrutivo.
#
# Uso:
#   bash _monitor_deploy/scripts/sync_zentra_overlay_into_climaticsystem.sh /path/to/ClimaticSystem
#
# Variáveis opcionais:
#   SKIP_BACKUP=1     — não cria backup (não recomendado)
#   BACKUP_ROOT=...   — pasta dos backups (default: <DEST>/.overlay_backups)
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
  echo "ERRO: overlay incompleto em $SRC (falta monitor_web/)" >&2
  exit 1
fi
HAVE_RSYNC=0
if command -v rsync >/dev/null 2>&1; then
  HAVE_RSYNC=1
else
  echo "AVISO: rsync ausente — usando cp -a (mesmo efeito; instale rsync se puder)."
fi

# Copia árvore de arquivos (preserva dest-only files: overlay additive/update)
_overlay_tree() {
  local from="$1" to="$2"
  mkdir -p "$to"
  if [[ "$HAVE_RSYNC" -eq 1 ]]; then
    rsync -a \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '.DS_Store' \
      "$from/" "$to/"
  else
    # cp -a: atualiza/cria tudo que existe no overlay; não apaga extras do ClimaticSystem
    cp -a "$from"/. "$to"/
    find "$to" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    find "$to" -type f -name '*.pyc' -delete 2>/dev/null || true
  fi
}

_backup_tree() {
  local from="$1" to="$2"
  mkdir -p "$to"
  if [[ "$HAVE_RSYNC" -eq 1 ]]; then
    rsync -a --delete \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      "$from/" "$to/"
  else
    rm -rf "$to"
    mkdir -p "$(dirname "$to")"
    cp -a "$from" "$to"
  fi
}

TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="${BACKUP_ROOT:-$DEST/.overlay_backups}"
BACKUP_DIR="$BACKUP_ROOT/pre_overlay_$TS"

echo "Overlay (completo): $SRC → $DEST"

if [[ "${SKIP_BACKUP:-0}" != "1" ]]; then
  mkdir -p "$BACKUP_DIR"
  echo "Backup → $BACKUP_DIR"
  _backup_tree "$DEST/monitor_web" "$BACKUP_DIR/monitor_web"
  [[ -f "$DEST/lib_efc.py" ]] && cp -a "$DEST/lib_efc.py" "$BACKUP_DIR/"
  [[ -f "$DEST/monitor_i18n.py" ]] && cp -a "$DEST/monitor_i18n.py" "$BACKUP_DIR/"
  [[ -d "$DEST/scripts" ]] && _backup_tree "$DEST/scripts" "$BACKUP_DIR/scripts" || true
  [[ -d "$DEST/docs" ]] && _backup_tree "$DEST/docs" "$BACKUP_DIR/docs" || true
  if [[ -d "$BACKUP_ROOT" ]]; then
    ls -1dt "$BACKUP_ROOT"/pre_overlay_* 2>/dev/null | tail -n +6 | while read -r old; do
      rm -rf "$old"
    done || true
  fi
else
  echo "SKIP_BACKUP=1 — sem backup"
fi

# --- monitor_web inteiro (services, static, templates, urls, views, tests, …)
_overlay_tree "$SRC/monitor_web" "$DEST/monitor_web"

# --- libs raiz usadas pelo monitor (se existirem no overlay)
if [[ -f "$SRC/lib_efc.py" ]]; then
  cp -a "$SRC/lib_efc.py" "$DEST/lib_efc.py"
fi
if [[ -f "$SRC/monitor_i18n.py" ]]; then
  cp -a "$SRC/monitor_i18n.py" "$DEST/monitor_i18n.py"
fi

# --- scripts / docs de preview local
mkdir -p "$DEST/scripts" "$DEST/docs"
if [[ -d "$SRC/scripts" ]]; then
  _overlay_tree "$SRC/scripts" "$DEST/scripts"
  chmod +x "$DEST/scripts/"*.sh 2>/dev/null || true
fi
if [[ -d "$SRC/docs" ]]; then
  _overlay_tree "$SRC/docs" "$DEST/docs"
fi

# Sanidade rápida
MISSING=0
for rel in \
  monitor_web/services/zentra_sensors.py \
  monitor_web/services/map_3d_overlay.py \
  monitor_web/services/sensor_series.py \
  monitor_web/static/monitor/js/monitor.js \
  monitor_web/static/monitor/js/map_clima_layer.js \
  monitor_web/static/monitor/css/monitor.css \
  monitor_web/templates/monitor/components/map_layer_strip.html \
  monitor_web/templates/monitor/sensores.html \
  monitor_web/templates/monitor/portal/sensores.html
do
  if [[ ! -f "$DEST/$rel" ]]; then
    echo "AVISO: faltou após overlay: $DEST/$rel" >&2
    MISSING=1
  fi
done

echo "OK. Overlay aplicado em $DEST"
[[ "$MISSING" -eq 0 ]] || echo "AVISO: alguns arquivos esperados faltam — confira o overlay GeoInfra." >&2
echo "Rode com USE_BQ=1 + EFC_MONITOR_DISABLE_AUTH=1:"
echo "  cd $DEST && ./scripts/run_django_monitor.sh"
echo "Ou o bootstrap único:"
echo "  bash $SRC/scripts/bootstrap_local_monitor_prodlike.sh"
