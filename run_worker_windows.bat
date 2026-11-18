@echo off
call .venv\Scripts\activate.bat
celery -A celery_app.celery worker --pool=solo --loglevel=info
