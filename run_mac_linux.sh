#!/usr/bin/env bash
cd "$(dirname "$0")"
export QB_DEBUG=1
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
