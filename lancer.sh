#!/usr/bin/env bash
# Lance l'interface locale. L'application coupe elle-même toute sortie réseau :
# télémétrie de Gradio désactivée, Hugging Face hors ligne sauf pendant un
# téléchargement de modèles demandé depuis l'interface.
set -euo pipefail
cd "$(dirname "$0")"
export GRADIO_ANALYTICS_ENABLED=False
exec .venv/bin/python -m mac_docling.app
