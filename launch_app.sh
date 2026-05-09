#!/usr/bin/env bash
# local-llm-hub - Streamlit control panel
set -euo pipefail
cd "$(dirname "$0")"
exec ./.venv/bin/python -m streamlit run app/app.py
