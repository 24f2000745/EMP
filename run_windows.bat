@echo off
if not exist .venv py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if not defined EMP_SECRET_KEY set EMP_SECRET_KEY=change-this-local-development-secret
if not defined EMP_ADMIN_EMAIL set EMP_ADMIN_EMAIL=admin@emp.local
if not defined EMP_ADMIN_PASSWORD set EMP_ADMIN_PASSWORD=Admin@123
if not defined REDIS_URL set REDIS_URL=redis://localhost:6379/0
python app.py
