@echo off
cd /d "%~dp0backend"

if not exist "venv\Scripts\python.exe" (
  echo Creando entorno virtual...
  python -m venv venv
)

echo Verificando dependencias...
.\venv\Scripts\python.exe -m pip install --ignore-requires-python -r requirements.txt

rem OCR en GPU: activar_gpu.bat y luego probar_gpu.bat (este deja la
rem configuracion en backend\gpu_config.json). Para forzar solo CPU:
rem set OCR_DISPOSITIVO=cpu

echo Iniciando servidor en http://localhost:8000 ...
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload


