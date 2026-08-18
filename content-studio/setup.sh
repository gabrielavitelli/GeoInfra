#!/usr/bin/env bash
# Instala dependências + venv Python para Manim
set -euo pipefail
cd "$(dirname "$0")"

echo "Pasta do projeto: $(pwd)"
echo ""

if command -v apt-get >/dev/null 2>&1; then
  echo "→ Dependências de sistema (pode pedir senha do sudo)..."
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    python3-venv libcairo2-dev libpango1.0-dev pkg-config python3-dev \
    ffmpeg texlive-latex-base texlive-fonts-extra texlive-latex-extra dvisvgm \
    || true
else
  echo "→ Sem apt-get. Instale ffmpeg, cairo, pango e LaTeX manualmente se o Manim falhar."
fi

echo "→ Ambiente virtual Python..."
PYTHON="${PYTHON:-python3}"
if [[ -n "${CONDA_PREFIX:-}" ]]; then
  echo "  (conda detectado: $CONDA_DEFAULT_ENV)"
fi

"$PYTHON" -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r manim/requirements.txt

echo ""
echo "Pronto. Rode a partir desta pasta:"
echo "  python3 scripts/conteudo.py"
echo "  ./render.sh derivada_tangente ql"
echo "  ./render.sh pitagoras_visual ql"
