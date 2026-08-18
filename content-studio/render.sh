#!/usr/bin/env bash
# Renderiza cenas Manim vinculadas aos posts
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SCENE="${1:-}"
QUALITY="${2:-ql}"  # ql = low quality (rápido), qh = high, qk = 4K

if [[ -z "$SCENE" ]]; then
  echo "Uso: ./render.sh <cena> [ql|qh|qk]"
  echo "Cenas disponíveis:"
  echo "  derivada_tangente  → post 001"
  echo "  pitagoras_visual   → post 002"
  exit 1
fi

FILE="$ROOT/manim/scenes/${SCENE}.py"
if [[ ! -f "$FILE" ]]; then
  echo "Cena não encontrada: $FILE"
  exit 1
fi

CLASS="$(python3 -c "
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r'class (\w+)\(Scene\)', text)
print(m.group(1) if m else sys.exit(1))
" "$FILE")"

VENV="$ROOT/.venv/bin/manim"
if [[ ! -x "$VENV" ]]; then
  echo "Venv não encontrado. Rode primeiro: ./setup.sh"
  exit 1
fi

mkdir -p "$ROOT/output/videos"
"$VENV" -"${QUALITY}" "$FILE" "$CLASS" --media_dir "$ROOT/output"

echo ""
echo "Vídeo salvo em $ROOT/output/videos/"
