#!/usr/bin/env bash
# Instala dependências de sistema + venv Python para Manim
set -euo pipefail
cd "$(dirname "$0")"

echo "→ Dependências de sistema (requer sudo)..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3.12-venv libcairo2-dev libpango1.0-dev pkg-config python3-dev \
  ffmpeg texlive-latex-base texlive-fonts-extra texlive-latex-extra dvisvgm

echo "→ Ambiente virtual Python..."
python3 -m venv .venv
.venv/bin/pip install -q -r manim/requirements.txt

echo ""
echo "Pronto. Renderizar:"
echo "  ./render.sh derivada_tangente ql"
echo "  ./render.sh pitagoras_visual ql"
