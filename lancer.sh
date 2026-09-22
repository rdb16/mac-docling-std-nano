#!/usr/bin/env bash
# Lance l'interface locale. Les deux variables coupent toute sortie réseau :
# la télémétrie de Gradio, et les appels de Hugging Face pour vérifier les
# révisions de modèles (les poids sont déjà en cache).
set -euo pipefail
cd "$(dirname "$0")"
export GRADIO_ANALYTICS_ENABLED=False
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
exec .venv/bin/python -m mac_docling.app
