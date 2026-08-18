#!/usr/bin/env bash
# Renderiza cenas Manim vinculadas aos posts
set -euo pipefail
cd "$(dirname "$0")/.."

SCENE="${1:-}"
QUALITY="${2:-ql}"  # ql = low quality (rápido), qh = high, qk = 4K

if [[ -z "$SCENE" ]]; then
  echo "Uso: ./render.sh <cena> [ql|qh|qk]"
  echo "Cenas disponíveis:"
  echo "  derivada_tangente  → post 001"
  echo "  pitagoras_visual   → post 002"
  exit 1
fi

FILE="manim/scenes/${SCENE}.py"
CLASS="$(python3 -c "
import re, sys
text = open('${FILE}').read()
m = re.search(r'class (\w+)\(Scene\)', text)
print(m.group(1) if m else sys.exit(1))
")"

mkdir -p output/videos
VENV="$(dirname "$0")/.venv/bin/manim"
if [[ ! -x "$VENV" ]]; then
  echo "Crie o venv: python3 -m venv .venv && .venv/bin/pip install -r manim/requirements.txt"
  exit 1
fi
"$VENV" -${QUALITY} "$FILE" "$CLASS" --media_dir output

echo ""
echo "Vídeo salvo em output/videos/"
