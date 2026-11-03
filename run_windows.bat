@echo off
setlocal
rem DEBUG on for the local runserver so CSS/static files are served (settings default is off).
set QB_DEBUG=1
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -m venv .venv || python -m venv .venv
)
set PY=.venv\Scripts\python.exe
%PY% -m pip install -r requirements.txt || goto :err
if exist db.sqlite3 (
  for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do copy /y db.sqlite3 "db.sqlite3.bak-%%d" >nul
)
%PY% manage.py backup_data
%PY% manage.py migrate || goto :err
start "" http://127.0.0.1:8000
%PY% manage.py runserver 127.0.0.1:8000
goto :eof
:err
echo.
echo Something failed - see the message above.
pause
