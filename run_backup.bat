@echo off
rem Automatic backup for QuantBud Invoice Studio. Schedule this file yourself if wanted (not done automatically).
cd /d "%~dp0"
.venv\Scripts\python.exe manage.py create_backup --kind auto --verify --retention
if errorlevel 1 (echo Backup FAILED - see above. & pause & exit /b 1)
