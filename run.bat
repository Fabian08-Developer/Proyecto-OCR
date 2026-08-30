@echo off
cd /d "%~dp0backend"

if not exist venv (
  echo Creando entorno virtual...
  python -m venv venv
)

call venv\Scripts\activate.bat
pip install --ignore-requires-python -r requirements.txt

echo Iniciando servidor en http://localhost:8000 ...
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

