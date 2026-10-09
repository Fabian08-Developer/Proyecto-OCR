@echo off
cd /d "%~dp0backend"

if not exist venv (
  echo Creando entorno virtual...
  python -m venv venv
)

call venv\Scripts\activate.bat
pip install --ignore-requires-python -r requirements.txt

rem OCR en GPU: se activa con activar_gpu.bat. Ajustes opcionales
rem (probar_gpu.bat dice cuales convienen en este equipo):
rem set OCR_DISPOSITIVO=cpu
rem set OCR_GPU_MODULOS=det,rec

echo Iniciando servidor en http://localhost:8000 ...
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

