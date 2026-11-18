# Examination Management Portal (EMP V2)

A local-first role-based examination management portal built for the MAD-2 EMP V2 specification.

## Implemented functionality

### Authentication and accounts
- Student self-registration and login/logout.
- Pre-created single administrator; no admin registration route.
- Admin-created examiner accounts.
- Password hashing, server-side sessions and role-protected APIs.
- Student and examiner profiles; admin user search and account activation/deactivation.

### Admin
- Dashboard statistics for courses, examinations, students, examiners, slots and bookings.
- Course create, update, list and safe deletion (courses with examinations must be deactivated).
- Examination create/update/list, course association, examiner assignment and timeline/status controls.
- Rubric criterion create/update/delete.
- Examiner management and student/examiner search.
- All slot and booking views, booking rescheduling and examiner reassignment.
- Result publication is blocked while any active booking has a pending evaluation.
- Summary statistics and most-active-examiner report.

### Examiner
- Assigned examination view.
- Slot creation during the configured slot-creation window; slot editing/cancellation before booking starts.
- Overlap and capacity validation.
- Booked-student view, rubric-based evaluation, marks and remarks.
- Mark bookings and examinations completed after evaluations are submitted.

### Student
- Examination discovery and search.
- Available slot view, booking and cancellation before the booking deadline.
- One active booking per examination, capacity protection and booking history.
- Published results and examiner remarks.
- Asynchronous examination-history CSV export and export-job download.

### Redis and Celery
- Redis-backed cache adapter for courses, examinations, schedules and rubrics. The app degrades gracefully when Redis is not running; the cache becomes active when Redis is available.
- Celery CSV export task.
- Daily examination reminder task (in-portal notification and optional SMTP email).
- Monthly HTML report email to the admin (SMTP configuration required).
- Celery Beat schedules reminders daily at 09:00 and monthly report on day 1 at 09:15, using Asia/Kolkata by default.

## Technology
- Flask REST API
- Vue 3 via CDN (no Node/Vue build step required)
- Bootstrap 5
- SQLite (created programmatically by `init_db()`)
- Redis
- Celery + Celery Beat

No PostgreSQL, MySQL, MongoDB, React, or alternative CSS framework is used.

## Requirements
- Python 3.10 or newer
- Redis server (required for Redis cache and Celery background jobs)
- Internet access in the browser for Vue, Bootstrap and Google Fonts CDN assets

## Quick start — Windows PowerShell

1. Extract the ZIP.
2. Open PowerShell in the project folder.
3. Create a virtual environment and install dependencies:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

4. Start Redis. One local option is to run Redis through WSL (Ubuntu):

```powershell
wsl
sudo service redis-server start
redis-cli ping
```

The expected response from `redis-cli ping` is `PONG`. Keep Redis running.

5. Copy `.env.example` to `.env` and edit the values, or configure the administrator and session secret in PowerShell before the first run:

```powershell
$env:EMP_SECRET_KEY = "replace-with-a-long-random-secret"
$env:EMP_ADMIN_EMAIL = "admin@emp.local"
$env:EMP_ADMIN_PASSWORD = "ChangeThisAdmin123!"
$env:REDIS_URL = "redis://localhost:6379/0"
python app.py
```

6. Open http://127.0.0.1:5000

The database and all tables are created automatically at first launch. The administrator account is inserted programmatically if one does not already exist.

### Start Celery on Windows

Keep Redis and Flask running. Open two additional PowerShell terminals in the project folder, activate `.venv` in each, and run:

Terminal 2 — worker:

```powershell
celery -A celery_app.celery worker --pool=solo --loglevel=info
```

Terminal 3 — scheduler:

```powershell
celery -A celery_app.celery beat --loglevel=info
```

The `solo` pool is recommended for local Windows development. Do not run multiple Beat schedulers for the same project, or scheduled reminders/reports may run more than once.

## Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo service redis-server start   # if using a system Redis installation
export EMP_SECRET_KEY='replace-with-a-long-random-secret'
export EMP_ADMIN_EMAIL='admin@emp.local'
export EMP_ADMIN_PASSWORD='ChangeThisAdmin123!'
export REDIS_URL='redis://localhost:6379/0'
python app.py
```

In separate terminals (with the same environment and virtual environment):

```bash
celery -A celery_app.celery worker --loglevel=info
celery -A celery_app.celery beat --loglevel=info
```

## Optional SMTP configuration

Email delivery is optional for local demos. Configure these environment variables to send daily reminders and monthly HTML reports:

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-app-password
SMTP_FROM=EMP Notifications <noreply@example.com>
```

Without SMTP settings, the daily reminder job still creates in-portal notifications and the monthly report task still calculates the report data, but email delivery is skipped. Use an SMTP app password where supported; never commit credentials.

## Default development administrator

If no environment variables are set on a clean database:

- Email: `admin@emp.local`
- Password: `Admin@123`

Change these before creating the database for any non-demo use. The admin is not exposed through registration. If the database already contains an admin, changing the environment variable does not reset that account's password.

## API overview

All JSON APIs are under `/api`. Authentication uses Flask sessions/cookies.

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | Public (student only) | Student registration |
| POST | `/api/auth/login` | Public | Login |
| POST | `/api/auth/logout` | Authenticated | Logout |
| GET/PUT | `/api/profile` | Authenticated | Read/update profile |
| GET | `/api/dashboard` | Authenticated | Role dashboard data |
| GET/POST | `/api/courses`, `/api/admin/courses` | Authenticated / Admin | Course listing/create |
| PUT/DELETE | `/api/admin/courses/<id>` | Admin | Update/delete course |
| GET | `/api/examinations` | Authenticated | Search/list examinations |
| POST | `/api/admin/examinations` | Admin | Create examination and assign examiners |
| PUT/DELETE | `/api/admin/examinations/<id>` | Admin | Update/delete examination |
| POST | `/api/admin/examinations/<id>/timeline-action` | Admin | Open/close workflow stages |
| GET/POST | `/api/examinations/<id>/rubrics`, `/api/admin/examinations/<id>/rubrics` | Authenticated / Admin | Read/create rubric |
| PUT/DELETE | `/api/admin/rubrics/<id>` | Admin | Update/delete rubric |
| GET | `/api/slots` | Authenticated | List role-appropriate slots |
| POST | `/api/examiner/slots` | Examiner | Create slot |
| PUT/DELETE | `/api/examiner/slots/<id>` | Examiner | Edit/cancel own slot |
| GET/POST | `/api/bookings`, `/api/student/bookings` | Authenticated / Student | List/book slots |
| POST | `/api/student/bookings/<id>/cancel` | Student | Cancel booking |
| POST | `/api/admin/bookings/<id>/reschedule` | Admin | Reschedule booking |
| POST | `/api/admin/bookings/<id>/reassign` | Admin | Reassign examiner |
| GET | `/api/examiner/evaluations` | Examiner | List evaluation work |
| POST | `/api/examiner/evaluations/<booking_id>` | Examiner | Submit marks/remarks |
| POST | `/api/examiner/bookings/<id>/complete` | Examiner | Complete evaluated booking |
| POST | `/api/admin/examinations/<id>/publish` | Admin | Publish results |
| GET | `/api/student/results` | Student | Published results |
| GET | `/api/admin/reports/summary` | Admin | Report statistics |
| POST/GET | `/api/student/exports` | Student | Queue/list CSV exports |
| GET | `/api/student/exports/<id>/download` | Student | Download own CSV |
| GET | `/api/notifications` | Authenticated | In-portal notifications |
| GET | `/api/health`, `/api/health/details` | Public | Service/Redis health |

## Important workflow

1. Admin creates a course.
2. Admin creates an examination, defines slot/booking windows, and assigns one or more examiners.
3. Admin changes the examination status to **Slot Creation**.
4. Assigned examiners create their slots within the slot-creation window.
5. Admin changes status to **Booking Open**.
6. Students book an available slot. The backend performs capacity decrement and duplicate-booking checks inside a SQLite write transaction.
7. Examiners evaluate booked students using the examination rubric and submit marks/remarks.
8. Examiners mark completed bookings/examinations; admin publishes results after all active bookings have submitted evaluations.
9. Students view published results and can request their CSV history export.

## Data and reset

- The default database is `emp.db` in the project directory.
- Override it with `EMP_DATABASE`.
- To reset a local demo, stop Flask and Celery and delete `emp.db`; the next `python app.py` run recreates the schema and admin account.
- Generated CSV files are stored under `instance/exports/` by default. Override with `EMP_EXPORT_DIR`.
- Do not delete the database while the server or worker is running.

## Testing and validation

Run the included unit tests after installing requirements:

```bash
python -m unittest discover -s tests -v
```

Syntax checks:

```bash
python -m py_compile app.py tasks.py celery_app.py cache_store.py
```

`/api/health/details` reports whether Redis is reachable. For Celery jobs, verify that the worker and Beat terminals remain running and that the task appears in the worker log.

