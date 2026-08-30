#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/backend"

if [ ! -d "venv" ]; then
  echo "Creando entorno virtual..."
  python3 -m venv venv
fi

source venv/bin/activate
pip install --ignore-requires-python -r requirements.txt

echo "Iniciando servidor en http://localhost:8000 ..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

