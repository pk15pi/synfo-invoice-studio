@echo off
cd /d "%~dp0"
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
py manage.py migrate
start http://127.0.0.1:8000
py manage.py runserver
pause
