@echo off
rem Instala la aceleracion por GPU (DirectML) para el OCR.
rem Para volver a solo CPU:  activar_gpu.bat --quitar
cd /d "%~dp0backend"

if not exist venv (
  echo Primero ejecuta run.bat una vez para crear el entorno virtual.
  pause
  exit /b 1
)

call venv\Scripts\activate.bat
python activar_gpu.py %*
pause
