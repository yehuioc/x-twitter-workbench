@echo off
cd /d "%~dp0"
python -X utf8 -B run_x.py serve --port 8915
pause
