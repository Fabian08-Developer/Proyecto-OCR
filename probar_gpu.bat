@echo off
rem Mide la velocidad del OCR en CPU vs GPU en este equipo.
cd /d "%~dp0backend"
call venv\Scripts\activate.bat
python benchmark_gpu.py %*
pause
