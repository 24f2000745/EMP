import os
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

REDIS_URL=os.environ.get('REDIS_URL','redis://localhost:6379/0')
celery=Celery('emp',broker=REDIS_URL,backend=REDIS_URL,include=['tasks'])
celery.conf.update(
    timezone=os.environ.get('EMP_TIMEZONE','Asia/Kolkata'),
    enable_utc=True,
    beat_schedule={
        'daily-exam-reminders': {'task':'tasks.send_daily_reminders','schedule':crontab(hour=9, minute=0)},
        'monthly-exam-report': {'task':'tasks.send_monthly_report','schedule':crontab(day_of_month=1, hour=9, minute=15)},
    },
    task_serializer='json', result_serializer='json', accept_content=['json'],
)
