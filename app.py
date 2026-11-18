import os
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE = Path(os.environ.get("EMP_DATABASE", BASE_DIR / "emp.db"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "EMP_SECRET_KEY", "change-this-development-secret-before-deployment"
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("EMP_HTTPS_ONLY", "0") == "1"
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','examiner','student')),
    full_name TEXT NOT NULL,
    phone TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    student_number TEXT UNIQUE,
    department TEXT,
    programme TEXT,
    semester INTEGER
);

CREATE TABLE IF NOT EXISTS examiner_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    department TEXT,
    designation TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS examinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    name TEXT NOT NULL,
    exam_type TEXT NOT NULL CHECK(exam_type IN ('Viva','Practical','Project Demo','Assessment')),
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0),
    max_marks REAL NOT NULL CHECK(max_marks > 0),
    instructions TEXT,
    slot_creation_start TEXT,
    slot_creation_end TEXT,
    booking_start TEXT,
    booking_end TEXT,
    status TEXT NOT NULL DEFAULT 'Draft'
        CHECK(status IN ('Draft','Slot Creation','Booking Open','Closed','Completed')),
    results_published INTEGER NOT NULL DEFAULT 0 CHECK(results_published IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS examiner_assignments (
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE CASCADE,
    examiner_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (examination_id, examiner_id)
);

CREATE TABLE IF NOT EXISTS rubrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE CASCADE,
    criterion_name TEXT NOT NULL,
    max_marks REAL NOT NULL CHECK(max_marks > 0),
    weightage REAL NOT NULL DEFAULT 1 CHECK(weightage > 0),
    description TEXT
);

CREATE TABLE IF NOT EXISTS exam_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE CASCADE,
    examiner_id INTEGER NOT NULL REFERENCES users(id),
    exam_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    max_capacity INTEGER NOT NULL CHECK(max_capacity > 0),
    available_seats INTEGER NOT NULL CHECK(available_seats >= 0),
    status TEXT NOT NULL DEFAULT 'Available'
        CHECK(status IN ('Available','Full','Cancelled','Completed')),
    meeting_link TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(available_seats <= max_capacity)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    slot_id INTEGER NOT NULL REFERENCES exam_slots(id),
    booked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'Booked'
        CHECK(status IN ('Booked','Cancelled','Completed')),
    UNIQUE(student_id, slot_id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id),
    student_id INTEGER NOT NULL REFERENCES users(id),
    examiner_id INTEGER NOT NULL REFERENCES users(id),
    total_marks REAL,
    remarks TEXT,
    evaluated_at TEXT,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK(status IN ('Pending','Submitted'))
);

CREATE TABLE IF NOT EXISTS evaluation_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    rubric_id INTEGER NOT NULL REFERENCES rubrics(id),
    marks_awarded REAL NOT NULL CHECK(marks_awarded >= 0),
    UNIQUE(evaluation_id, rubric_id)
);

CREATE TABLE IF NOT EXISTS result_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    examination_id INTEGER NOT NULL REFERENCES examinations(id),
    published_by INTEGER NOT NULL REFERENCES users(id),
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(examination_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS export_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'Queued'
        CHECK(status IN ('Queued','Running','Completed','Failed')),
    file_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_examinations_status ON examinations(status);
CREATE INDEX IF NOT EXISTS idx_slots_exam_date ON exam_slots(examination_id, exam_date);
CREATE INDEX IF NOT EXISTS idx_bookings_student ON bookings(student_id, status);
CREATE INDEX IF NOT EXISTS idx_evaluations_status ON evaluations(status);
"""


def connect_db():
    """Create a SQLite connection for the current request."""
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    """Create all tables and insert the single administrator if absent."""
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_db()
    try:
        conn.executescript(SCHEMA)
        admin_email = os.environ.get("EMP_ADMIN_EMAIL", "admin@emp.local").strip().lower()
        admin_password = os.environ.get("EMP_ADMIN_PASSWORD", "Admin@123")
        admin_exists = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()

        if admin_exists is None:
            conn.execute(
                """
                INSERT INTO users (email, password_hash, role, full_name)
                VALUES (?, ?, 'admin', ?)
                """,
                (
                    admin_email,
                    generate_password_hash(admin_password),
                    "EMP Administrator",
                ),
            )
        conn.commit()
    finally:
        conn.close()


@app.before_request
def open_connection():
    g.db = connect_db()


@app.teardown_request
def close_connection(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def api_error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def login_required(view):
    """Require a logged-in, active account."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return api_error("Please log in first.", 401)

        user = g.db.execute(
            """
            SELECT id, email, role, full_name, phone, is_active
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user is None or not user["is_active"]:
            session.clear()
            return api_error("Account is unavailable. Please log in again.", 401)

        g.current_user = dict(user)
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    """Allow only authenticated users whose role is in the supplied roles."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.current_user["role"] not in roles:
                return api_error(
                    "You do not have permission to perform this action.", 403
                )
            return view(*args, **kwargs)

        return wrapped

    return decorator


def audit(action, entity_type, entity_id=None, details=None):
    """Record important user actions for administrator review."""
    actor_id = session.get("user_id")
    g.db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, entity_type, entity_id, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (actor_id, action, entity_type, entity_id, details),
    )
    g.db.commit()


def get_public_user(user_id):
    row = g.db.execute(
        """
        SELECT id, email, role, full_name, phone, created_at
        FROM users WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "EMP V2", "database": "sqlite"})


@app.post("/api/auth/register")
def register():
    """Public registration is restricted to students."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    student_number = str(data.get("student_number", "")).strip() or None
    department = str(data.get("department", "")).strip() or None
    programme = str(data.get("programme", "")).strip() or None
    semester_value = data.get("semester")
    semester = int(semester_value) if str(semester_value or "").isdigit() else None

    if len(name) < 2:
        return api_error("Enter your full name.")
    if "@" not in email or len(email) > 254:
        return api_error("Enter a valid email address.")
    if len(password) < 8:
        return api_error("Password must contain at least 8 characters.")
    if semester is not None and not 1 <= semester <= 20:
        return api_error("Semester must be between 1 and 20.")

    try:
        cur = g.db.execute(
            """
            INSERT INTO users (email, password_hash, role, full_name)
            VALUES (?, ?, 'student', ?)
            """,
            (email, generate_password_hash(password), name),
        )
        user_id = cur.lastrowid
        g.db.execute(
            """
            INSERT INTO student_profiles
                (user_id, student_number, department, programme, semester)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, student_number, department, programme, semester),
        )
        g.db.commit()
    except sqlite3.IntegrityError as exc:
        g.db.rollback()
        if "email" in str(exc).lower():
            return api_error("An account with this email already exists.", 409)
        return api_error(
            "Student number is already in use or profile data is invalid.", 409
        )

    session.clear()
    session["user_id"] = user_id
    audit("student_registered", "user", user_id)
    return jsonify({"ok": True, "user": get_public_user(user_id)}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    user = g.db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if (
        user is None
        or not check_password_hash(user["password_hash"], password)
        or not user["is_active"]
    ):
        return api_error("Invalid email or password.", 401)

    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"ok": True, "user": get_public_user(user["id"])})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True, "message": "Logged out."})


@app.get("/api/auth/me")
@login_required
def me():
    return jsonify({"ok": True, "user": get_public_user(g.current_user["id"])})


@app.put("/api/profile")
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    name = str(data.get("full_name", g.current_user["full_name"])).strip()
    phone = str(data.get("phone", "")).strip() or None

    if len(name) < 2:
        return api_error("Full name must contain at least 2 characters.")

    g.db.execute(
        "UPDATE users SET full_name = ?, phone = ? WHERE id = ?",
        (name, phone, g.current_user["id"]),
    )

    if g.current_user["role"] == "student":
        student_number = str(data.get("student_number", "")).strip() or None
        department = str(data.get("department", "")).strip() or None
        programme = str(data.get("programme", "")).strip() or None
        semester_value = data.get("semester")
        semester = int(semester_value) if str(semester_value or "").isdigit() else None

        if semester is not None and not 1 <= semester <= 20:
            g.db.rollback()
            return api_error("Semester must be between 1 and 20.")

        try:
            g.db.execute(
                """
                UPDATE student_profiles
                SET student_number = ?, department = ?, programme = ?, semester = ?
                WHERE user_id = ?
                """,
                (
                    student_number,
                    department,
                    programme,
                    semester,
                    g.current_user["id"],
                ),
            )
        except sqlite3.IntegrityError:
            g.db.rollback()
            return api_error("That student number is already in use.", 409)

    g.db.commit()
    return jsonify({"ok": True, "user": get_public_user(g.current_user["id"])})


@app.get("/api/dashboard")
@login_required
def dashboard():
    role = g.current_user["role"]

    if role == "admin":
        counts = {
            "courses": g.db.execute("SELECT COUNT(*) n FROM courses").fetchone()["n"],
            "examinations": g.db.execute(
                "SELECT COUNT(*) n FROM examinations"
            ).fetchone()["n"],
            "students": g.db.execute(
                "SELECT COUNT(*) n FROM users WHERE role = 'student'"
            ).fetchone()["n"],
            "examiners": g.db.execute(
                "SELECT COUNT(*) n FROM users WHERE role = 'examiner'"
            ).fetchone()["n"],
            "slots": g.db.execute(
                "SELECT COUNT(*) n FROM exam_slots"
            ).fetchone()["n"],
            "bookings": g.db.execute(
                "SELECT COUNT(*) n FROM bookings WHERE status = 'Booked'"
            ).fetchone()["n"],
        }
        recent = g.db.execute(
            """
            SELECT e.id, e.name, e.exam_type, e.status,
                   c.code AS course_code, c.name AS course_name
            FROM examinations e
            JOIN courses c ON c.id = e.course_id
            ORDER BY e.id DESC LIMIT 5
            """
        ).fetchall()
        return jsonify({
            "ok": True,
            "role": role,
            "counts": counts,
            "recent_examinations": [dict(row) for row in recent],
        })

    if role == "examiner":
        user_id = g.current_user["id"]
        assigned = g.db.execute(
            """
            SELECT e.id, e.name, e.status, c.code AS course_code
            FROM examinations e
            JOIN courses c ON c.id = e.course_id
            JOIN examiner_assignments a ON a.examination_id = e.id
            WHERE a.examiner_id = ?
            ORDER BY e.id DESC
            """,
            (user_id,),
        ).fetchall()
        slot_count = g.db.execute(
            "SELECT COUNT(*) n FROM exam_slots WHERE examiner_id = ?",
            (user_id,),
        ).fetchone()["n"]
        booked_count = g.db.execute(
            """
            SELECT COUNT(*) n FROM bookings b
            JOIN exam_slots s ON s.id = b.slot_id
            WHERE s.examiner_id = ? AND b.status = 'Booked'
            """,
            (user_id,),
        ).fetchone()["n"]
        pending_count = g.db.execute(
            """
            SELECT COUNT(*) n FROM evaluations
            WHERE examiner_id = ? AND status = 'Pending'
            """,
            (user_id,),
        ).fetchone()["n"]

        return jsonify({
            "ok": True,
            "role": role,
            "counts": {
                "assigned_examinations": len(assigned),
                "slots": slot_count,
                "booked_students": booked_count,
                "pending_evaluations": pending_count,
            },
            "assigned_examinations": [dict(row) for row in assigned],
        })

    user_id = g.current_user["id"]
    upcoming = g.db.execute(
        """
        SELECT b.id AS booking_id, b.status AS booking_status,
               s.exam_date, s.start_time, s.end_time,
               e.name AS examination_name, c.code AS course_code,
               u.full_name AS examiner_name
        FROM bookings b
        JOIN exam_slots s ON s.id = b.slot_id
        JOIN examinations e ON e.id = s.examination_id
        JOIN courses c ON c.id = e.course_id
        JOIN users u ON u.id = s.examiner_id
        WHERE b.student_id = ? AND b.status = 'Booked'
        ORDER BY s.exam_date, s.start_time LIMIT 10
        """,
        (user_id,),
    ).fetchall()

    available = g.db.execute(
        """
        SELECT e.id, e.name, e.exam_type, e.duration_minutes, e.max_marks,
               e.status, c.code AS course_code, c.name AS course_name
        FROM examinations e
        JOIN courses c ON c.id = e.course_id
        WHERE e.status IN ('Slot Creation', 'Booking Open')
        ORDER BY e.id DESC LIMIT 10
        """
    ).fetchall()

    results = g.db.execute(
        """
        SELECT e.name AS examination_name, c.code AS course_code,
               v.total_marks, v.remarks, v.evaluated_at
        FROM evaluations v
        JOIN bookings b ON b.id = v.booking_id
        JOIN exam_slots s ON s.id = b.slot_id
        JOIN examinations e ON e.id = s.examination_id
        JOIN courses c ON c.id = e.course_id
        WHERE v.student_id = ? AND v.status = 'Submitted'
          AND e.results_published = 1
        ORDER BY v.evaluated_at DESC LIMIT 10
        """,
        (user_id,),
    ).fetchall()

    return jsonify({
        "ok": True,
        "role": role,
        "counts": {
            "available_examinations": len(available),
            "upcoming_bookings": len(upcoming),
            "published_results": len(results),
        },
        "available_examinations": [dict(row) for row in available],
        "upcoming_bookings": [dict(row) for row in upcoming],
        "published_results": [dict(row) for row in results],
    })


@app.get("/api/admin/users")
@roles_required("admin")
def list_users():
    role_filter = request.args.get("role", "").strip()
    query = request.args.get("q", "").strip()

    sql = """
        SELECT id, email, role, full_name, phone, is_active, created_at
        FROM users WHERE role != 'admin'
    """
    params = []

    if role_filter in ("student", "examiner"):
        sql += " AND role = ?"
        params.append(role_filter)

    if query:
        sql += " AND (full_name LIKE ? OR email LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])

    sql += " ORDER BY role, full_name LIMIT 200"
    rows = g.db.execute(sql, params).fetchall()
    return jsonify({"ok": True, "users": [dict(row) for row in rows]})


@app.post("/api/admin/examiners")
@roles_required("admin")
def create_examiner():
    data = request.get_json(silent=True) or {}
    name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    department = str(data.get("department", "")).strip() or None
    designation = str(data.get("designation", "")).strip() or None

    if len(name) < 2 or "@" not in email or len(password) < 8:
        return api_error(
            "Name, valid email, and password of at least 8 characters are required."
        )

    try:
        cur = g.db.execute(
            """
            INSERT INTO users (email, password_hash, role, full_name)
            VALUES (?, ?, 'examiner', ?)
            """,
            (email, generate_password_hash(password), name),
        )
        user_id = cur.lastrowid
        g.db.execute(
            """
            INSERT INTO examiner_profiles (user_id, department, designation)
            VALUES (?, ?, ?)
            """,
            (user_id, department, designation),
        )
        g.db.commit()
    except sqlite3.IntegrityError:
        g.db.rollback()
        return api_error("An account with that email already exists.", 409)

    audit("examiner_created", "user", user_id)
    return jsonify({"ok": True, "user": get_public_user(user_id)}), 201


@app.patch("/api/admin/users/<int:user_id>/status")
@roles_required("admin")
def set_user_status(user_id):
    data = request.get_json(silent=True) or {}
    active = data.get("is_active")

    if not isinstance(active, bool):
        return api_error("is_active must be true or false.")

    cursor = g.db.execute(
        "UPDATE users SET is_active = ? WHERE id = ? AND role != 'admin'",
        (int(active), user_id),
    )
    g.db.commit()

    if cursor.rowcount == 0:
        return api_error(
            "User not found or the administrator account cannot be modified.", 404
        )

    audit("user_status_changed", "user", user_id, str(active))
    return jsonify({"ok": True})


@app.get("/api/admin/audit")
@roles_required("admin")
def audit_history():
    rows = g.db.execute(
        """
        SELECT a.id, a.action, a.entity_type, a.entity_id, a.details,
               a.created_at, u.full_name AS actor
        FROM audit_logs a
        LEFT JOIN users u ON u.id = a.actor_id
        ORDER BY a.id DESC LIMIT 100
        """
    ).fetchall()
    return jsonify({"ok": True, "logs": [dict(row) for row in rows]})


# ---------------------------------------------------------------------------
# EMP V2 workflow APIs: academic setup, scheduling, bookings and evaluation.
# ---------------------------------------------------------------------------
def _body():
    return request.get_json(silent=True) or {}

def _cache_get(key):
    try:
        from cache_store import cache_get
        return cache_get(key)
    except Exception:
        return None

def _cache_set(key, value, ttl=60):
    try:
        from cache_store import cache_set
        cache_set(key, value, ttl)
    except Exception:
        pass

def _cache_clear(prefix=None):
    try:
        from cache_store import cache_clear
        cache_clear(prefix)
    except Exception:
        pass

def _audit(action, entity, entity_id, details=''):
    audit(action, entity, entity_id, details)

def _examiner_assigned(exam_id, examiner_id):
    return g.db.execute('SELECT 1 FROM examiner_assignments WHERE examination_id=? AND examiner_id=?', (exam_id, examiner_id)).fetchone() is not None

def _exam(exam_id):
    return g.db.execute('SELECT e.*, c.code course_code, c.name course_name FROM examinations e JOIN courses c ON c.id=e.course_id WHERE e.id=?', (exam_id,)).fetchone()

@app.get('/api/courses')
@login_required
def courses_list():
    cached = _cache_get('courses:all')
    if cached is not None: return jsonify({'ok': True, 'courses': cached})
    rows = [dict(r) for r in g.db.execute('SELECT * FROM courses ORDER BY code').fetchall()]
    _cache_set('courses:all', rows, 90)
    return jsonify({'ok': True, 'courses': rows})

@app.post('/api/admin/courses')
@roles_required('admin')
def course_create():
    d=_body(); code=str(d.get('code','')).strip().upper(); name=str(d.get('name','')).strip()
    if not code or not name: return api_error('Course code and name are required.')
    try:
        cur=g.db.execute('INSERT INTO courses(code,name,description,status) VALUES(?,?,?,?)',(code,name,str(d.get('description','')).strip(),d.get('status','Active'))); g.db.commit()
    except sqlite3.IntegrityError: return api_error('Course code already exists.')
    _cache_clear('courses:'); _cache_clear('exams:'); _audit('course_created','course',cur.lastrowid,code)
    return jsonify({'ok':True,'id':cur.lastrowid}),201

@app.put('/api/admin/courses/<int:course_id>')
@roles_required('admin')
def course_update(course_id):
    d=_body(); code=str(d.get('code','')).strip().upper(); name=str(d.get('name','')).strip()
    if not code or not name: return api_error('Course code and name are required.')
    try:
        cur=g.db.execute('UPDATE courses SET code=?,name=?,description=?,status=? WHERE id=?',(code,name,d.get('description',''),d.get('status','Active'),course_id)); g.db.commit()
    except sqlite3.IntegrityError: return api_error('Course code already exists.')
    if not cur.rowcount: return api_error('Course not found.',404)
    _cache_clear('courses:'); _cache_clear('exams:'); _audit('course_updated','course',course_id,code); return jsonify({'ok':True})

@app.delete('/api/admin/courses/<int:course_id>')
@roles_required('admin')
def course_delete(course_id):
    if g.db.execute('SELECT 1 FROM examinations WHERE course_id=?',(course_id,)).fetchone(): return api_error('Course has examinations; deactivate it instead.')
    cur=g.db.execute('DELETE FROM courses WHERE id=?',(course_id,)); g.db.commit()
    if not cur.rowcount: return api_error('Course not found.',404)
    _cache_clear('courses:'); _cache_clear('exams:'); _audit('course_deleted','course',course_id); return jsonify({'ok':True})

@app.get('/api/examinations')
@login_required
def examinations_list():
    q=request.args.get('q','').strip(); status=request.args.get('status','').strip(); params=[]
    cache_key=f"exams:{g.current_user['role']}:{session.get('user_id')}:{q}:{status}"
    cached=_cache_get(cache_key)
    if cached is not None: return jsonify({'ok':True,'examinations':cached})
    sql='''SELECT e.*,c.code course_code,c.name course_name FROM examinations e JOIN courses c ON c.id=e.course_id WHERE 1=1'''
    if g.current_user['role']=='examiner': sql+=' AND EXISTS(SELECT 1 FROM examiner_assignments a WHERE a.examination_id=e.id AND a.examiner_id=?)'; params.append(session['user_id'])
    if g.current_user['role']=='student': sql+=" AND e.status IN ('Slot Creation','Booking Open','Closed','Completed')"
    if q: sql+=' AND (e.name LIKE ? OR c.code LIKE ? OR c.name LIKE ?)'; params += [f'%{q}%']*3
    if status: sql+=' AND e.status=?'; params.append(status)
    sql+=' ORDER BY e.id DESC'
    rows=[dict(r) for r in g.db.execute(sql,params).fetchall()]
    _cache_set(cache_key,rows,45)
    return jsonify({'ok':True,'examinations':rows})

@app.post('/api/admin/examinations')
@roles_required('admin')
def examination_create():
    d=_body(); required=['course_id','name','exam_type','duration_minutes','max_marks']
    if any(d.get(k) in (None,'') for k in required): return api_error('Course, name, type, duration and maximum marks are required.')
    try:
        cur=g.db.execute('''INSERT INTO examinations(course_id,name,exam_type,duration_minutes,max_marks,instructions,slot_creation_start,slot_creation_end,booking_start,booking_end,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(int(d['course_id']),str(d['name']).strip(),d['exam_type'],int(d['duration_minutes']),float(d['max_marks']),d.get('instructions',''),d.get('slot_creation_start'),d.get('slot_creation_end'),d.get('booking_start'),d.get('booking_end'),d.get('status','Draft')))
        exam_id=cur.lastrowid
        for eid in d.get('examiner_ids',[]):
            g.db.execute("INSERT OR IGNORE INTO examiner_assignments(examination_id,examiner_id) SELECT ?,id FROM users WHERE id=? AND role='examiner'",(exam_id,int(eid)))
        g.db.commit()
    except (ValueError,sqlite3.IntegrityError): return api_error('Invalid examination details or course.')
    _cache_clear('exams:'); _audit('examination_created','examination',exam_id); return jsonify({'ok':True,'id':exam_id}),201

@app.get('/api/admin/examinations/<int:exam_id>/assignments')
@roles_required('admin')
def examination_assignments(exam_id):
    if not _exam(exam_id): return api_error('Examination not found.',404)
    rows=[r['examiner_id'] for r in g.db.execute('SELECT examiner_id FROM examiner_assignments WHERE examination_id=?',(exam_id,)).fetchall()]
    return jsonify({'ok':True,'examiner_ids':rows})

@app.put('/api/admin/examinations/<int:exam_id>')
@roles_required('admin')
def examination_update(exam_id):
    d=_body(); allowed=['course_id','name','exam_type','duration_minutes','max_marks','instructions','slot_creation_start','slot_creation_end','booking_start','booking_end','status']
    old=_exam(exam_id)
    if not old: return api_error('Examination not found.',404)
    vals={k:d.get(k,old[k]) for k in allowed}
    try:
        g.db.execute('''UPDATE examinations SET course_id=?,name=?,exam_type=?,duration_minutes=?,max_marks=?,instructions=?,slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=?,status=? WHERE id=?''',tuple(vals[k] for k in allowed)+(exam_id,))
        if 'examiner_ids' in d:
            g.db.execute('DELETE FROM examiner_assignments WHERE examination_id=?',(exam_id,))
            for eid in d['examiner_ids']: g.db.execute("INSERT OR IGNORE INTO examiner_assignments(examination_id,examiner_id) SELECT ?,id FROM users WHERE id=? AND role='examiner'",(exam_id,int(eid)))
        g.db.commit()
    except (ValueError,sqlite3.IntegrityError): return api_error('Invalid examination update.')
    _cache_clear('exams:'); _audit('examination_updated','examination',exam_id); return jsonify({'ok':True})

@app.delete('/api/admin/examinations/<int:exam_id>')
@roles_required('admin')
def examination_delete(exam_id):
    if g.db.execute('SELECT 1 FROM bookings b JOIN exam_slots s ON s.id=b.slot_id WHERE s.examination_id=?',(exam_id,)).fetchone(): return api_error('Examination has booking history and cannot be deleted.')
    cur=g.db.execute('DELETE FROM examinations WHERE id=?',(exam_id,)); g.db.commit()
    if not cur.rowcount: return api_error('Examination not found.',404)
    _cache_clear('exams:'); _audit('examination_deleted','examination',exam_id); return jsonify({'ok':True})

@app.get('/api/admin/examiners')
@roles_required('admin')
def examiners_list():
    rows=[dict(r) for r in g.db.execute("SELECT u.id,u.full_name,u.email,u.is_active,p.department,p.designation FROM users u LEFT JOIN examiner_profiles p ON p.user_id=u.id WHERE u.role='examiner' ORDER BY u.full_name").fetchall()]
    return jsonify({'ok':True,'examiners':rows})

@app.get('/api/examinations/<int:exam_id>/rubrics')
@login_required
def rubrics_list(exam_id):
    cached=_cache_get(f'rubrics:{exam_id}')
    if cached is not None: return jsonify({'ok':True,'rubrics':cached})
    if not _exam(exam_id): return api_error('Examination not found.',404)
    rows=[dict(r) for r in g.db.execute('SELECT * FROM rubrics WHERE examination_id=? ORDER BY id',(exam_id,)).fetchall()]
    _cache_set(f'rubrics:{exam_id}',rows,120)
    return jsonify({'ok':True,'rubrics':rows})

@app.post('/api/admin/examinations/<int:exam_id>/rubrics')
@roles_required('admin')
def rubric_create(exam_id):
    d=_body(); name=str(d.get('criterion_name','')).strip()
    try: marks=float(d.get('max_marks',0)); weight=float(d.get('weightage',1))
    except (ValueError,TypeError): return api_error('Marks and weightage must be numeric.')
    exam=_exam(exam_id)
    if not name or marks<=0 or weight<=0 or not exam: return api_error('Valid criterion, marks, weightage and examination are required.')
    current=g.db.execute('SELECT COALESCE(SUM(max_marks),0) FROM rubrics WHERE examination_id=?',(exam_id,)).fetchone()[0]
    if current+marks>exam['max_marks']: return api_error('The total rubric marks cannot exceed the examination maximum marks.')
    cur=g.db.execute('INSERT INTO rubrics(examination_id,criterion_name,max_marks,weightage,description) VALUES(?,?,?,?,?)',(exam_id,name,marks,weight,d.get('description',''))); g.db.commit()
    _cache_clear('rubrics:'); _audit('rubric_created','rubric',cur.lastrowid); return jsonify({'ok':True,'id':cur.lastrowid}),201

@app.put('/api/admin/rubrics/<int:rubric_id>')
@roles_required('admin')
def rubric_update(rubric_id):
    d=_body(); cur=g.db.execute('UPDATE rubrics SET criterion_name=?,max_marks=?,weightage=?,description=? WHERE id=?',(str(d.get('criterion_name','')).strip(),float(d.get('max_marks',0)),float(d.get('weightage',1)),d.get('description',''),rubric_id)); g.db.commit()
    if not cur.rowcount: return api_error('Rubric not found.',404)
    _cache_clear('rubrics:'); return jsonify({'ok':True})

@app.delete('/api/admin/rubrics/<int:rubric_id>')
@roles_required('admin')
def rubric_delete(rubric_id):
    if g.db.execute('SELECT 1 FROM evaluation_scores WHERE rubric_id=?',(rubric_id,)).fetchone(): return api_error('Rubric is already used in an evaluation.')
    cur=g.db.execute('DELETE FROM rubrics WHERE id=?',(rubric_id,)); g.db.commit()
    if not cur.rowcount: return api_error('Rubric not found.',404)
    _cache_clear('rubrics:'); return jsonify({'ok':True})

@app.get('/api/slots')
@login_required
def slots_list():
    exam_id=request.args.get('examination_id'); params=[]
    cache_key=f"slots:{g.current_user['role']}:{session.get('user_id')}:{exam_id or ''}"
    cached=_cache_get(cache_key)
    if cached is not None: return jsonify({'ok':True,'slots':cached})
    sql='''SELECT s.*,e.name examination_name,e.status examination_status,u.full_name examiner_name FROM exam_slots s JOIN examinations e ON e.id=s.examination_id JOIN users u ON u.id=s.examiner_id WHERE 1=1'''
    if exam_id: sql+=' AND s.examination_id=?'; params.append(exam_id)
    if g.current_user['role']=='examiner': sql+=' AND s.examiner_id=?'; params.append(session['user_id'])
    if g.current_user['role']=='student': sql+=" AND s.status='Available' AND s.available_seats>0 AND e.status='Booking Open'"
    sql+=' ORDER BY s.exam_date,s.start_time'
    rows=[dict(r) for r in g.db.execute(sql,params).fetchall()]
    _cache_set(cache_key,rows,25)
    return jsonify({'ok':True,'slots':rows})

@app.post('/api/examiner/slots')
@roles_required('examiner')
def slot_create():
    d=_body(); eid=d.get('examination_id'); exam=_exam(eid) if str(eid or '').isdigit() else None
    if not exam or not _examiner_assigned(eid,session['user_id']): return api_error('You are not assigned to this examination.',403)
    if exam['status']!='Slot Creation': return api_error('Slot creation is not currently open.')
    now_sql="datetime('now','localtime')"
    if exam['slot_creation_start'] and g.db.execute("SELECT datetime(?) > datetime('now','localtime')",(exam['slot_creation_start'],)).fetchone()[0]: return api_error('Slot creation period has not started.')
    if exam['slot_creation_end'] and g.db.execute("SELECT datetime(?) < datetime('now','localtime')",(exam['slot_creation_end'],)).fetchone()[0]: return api_error('Slot creation period has ended.')
    date=d.get('exam_date'); start=d.get('start_time'); end=d.get('end_time')
    try: cap=int(d.get('max_capacity',1))
    except: cap=0
    if not date or not start or not end or end<=start or cap<1: return api_error('Enter a valid date, time range and positive capacity.')
    conflict=g.db.execute("SELECT 1 FROM exam_slots WHERE examiner_id=? AND exam_date=? AND status!='Cancelled' AND start_time<? AND end_time>?",(session['user_id'],date,end,start)).fetchone()
    if conflict: return api_error('This overlaps another slot in your schedule.')
    cur=g.db.execute('INSERT INTO exam_slots(examination_id,examiner_id,exam_date,start_time,end_time,max_capacity,available_seats,meeting_link) VALUES(?,?,?,?,?,?,?,?)',(eid,session['user_id'],date,start,end,cap,cap,d.get('meeting_link',''))); g.db.commit()
    _cache_clear('slots:'); _audit('slot_created','slot',cur.lastrowid); return jsonify({'ok':True,'id':cur.lastrowid}),201

@app.put('/api/examiner/slots/<int:slot_id>')
@roles_required('examiner')
def slot_update(slot_id):
    s=g.db.execute('SELECT * FROM exam_slots WHERE id=? AND examiner_id=?',(slot_id,session['user_id'])).fetchone()
    if not s: return api_error('Slot not found.',404)
    ex=_exam(s['examination_id'])
    if ex['status']!='Slot Creation' or g.db.execute("SELECT 1 FROM bookings WHERE slot_id=? AND status='Booked'",(slot_id,)).fetchone(): return api_error('Slots cannot be changed after booking begins or a booking exists.')
    d=_body(); date=d.get('exam_date',s['exam_date']); start=d.get('start_time',s['start_time']); end=d.get('end_time',s['end_time']); cap=int(d.get('max_capacity',s['max_capacity']))
    if end<=start or cap<1: return api_error('Invalid slot time or capacity.')
    conflict=g.db.execute("SELECT 1 FROM exam_slots WHERE examiner_id=? AND exam_date=? AND id!=? AND status!='Cancelled' AND start_time<? AND end_time>?",(session['user_id'],date,slot_id,end,start)).fetchone()
    if conflict: return api_error('This overlaps another slot in your schedule.')
    g.db.execute('UPDATE exam_slots SET exam_date=?,start_time=?,end_time=?,max_capacity=?,available_seats=?,meeting_link=? WHERE id=?',(date,start,end,cap,cap,d.get('meeting_link',s['meeting_link']),slot_id)); g.db.commit(); _cache_clear('slots:'); return jsonify({'ok':True})

@app.delete('/api/examiner/slots/<int:slot_id>')
@roles_required('examiner')
def slot_delete(slot_id):
    s=g.db.execute('SELECT * FROM exam_slots WHERE id=? AND examiner_id=?',(slot_id,session['user_id'])).fetchone()
    if not s: return api_error('Slot not found.',404)
    if _exam(s['examination_id'])['status']!='Slot Creation' or g.db.execute("SELECT 1 FROM bookings WHERE slot_id=? AND status='Booked'",(slot_id,)).fetchone(): return api_error('This slot can no longer be deleted.')
    g.db.execute("UPDATE exam_slots SET status='Cancelled',available_seats=0 WHERE id=?",(slot_id,)); g.db.commit(); _cache_clear('slots:'); return jsonify({'ok':True})

@app.post('/api/student/bookings')
@roles_required('student')
def booking_create():
    d=_body(); sid=d.get('slot_id')
    slot=g.db.execute('SELECT s.*,e.status exam_status,e.booking_start,e.booking_end,e.id exam_id,e.name exam_name FROM exam_slots s JOIN examinations e ON e.id=s.examination_id WHERE s.id=?',(sid,)).fetchone()
    if not slot or slot['status']!='Available' or slot['available_seats']<1: return api_error('This slot is no longer available.')
    if slot['exam_status']!='Booking Open': return api_error('Booking is not open for this examination.')
    now=g.db.execute("SELECT datetime('now','localtime')").fetchone()[0]
    if slot['booking_start'] and g.db.execute("SELECT datetime('now','localtime') < datetime(?)",(slot['booking_start'],)).fetchone()[0]: return api_error('Booking period has not started.')
    if slot['booking_end'] and g.db.execute("SELECT datetime('now','localtime') > datetime(?)",(slot['booking_end'],)).fetchone()[0]: return api_error('Booking deadline has passed.')
    if g.db.execute("SELECT 1 FROM bookings b JOIN exam_slots s ON s.id=b.slot_id WHERE b.student_id=? AND s.examination_id=? AND b.status='Booked'",(session['user_id'],slot['exam_id'])).fetchone(): return api_error('You already have a booking for this examination.')
    try:
        g.db.execute('BEGIN IMMEDIATE')
        duplicate=g.db.execute("SELECT 1 FROM bookings b JOIN exam_slots sx ON sx.id=b.slot_id WHERE b.student_id=? AND sx.examination_id=? AND b.status='Booked'",(session['user_id'],slot['exam_id'])).fetchone()
        if duplicate: g.db.rollback(); return api_error('You already have a booking for this examination.')
        changed=g.db.execute("UPDATE exam_slots SET available_seats=available_seats-1,status=CASE WHEN available_seats-1=0 THEN 'Full' ELSE 'Available' END WHERE id=? AND status='Available' AND available_seats>0",(sid,)).rowcount
        if not changed: g.db.rollback(); return api_error('This slot has just been booked by someone else.')
        cur=g.db.execute("INSERT INTO bookings(student_id,slot_id,status) VALUES(?,?,'Booked')",(session['user_id'],sid))
        g.db.execute("INSERT INTO evaluations(booking_id,student_id,examiner_id,status) VALUES(?,?,?,'Pending')",(cur.lastrowid,session['user_id'],slot['examiner_id']))
        g.db.execute('INSERT INTO notifications(user_id,title,message) VALUES(?,?,?)',(session['user_id'],'Booking confirmed',f"Your {slot['exam_name']} booking is confirmed."))
        g.db.commit()
    except sqlite3.IntegrityError:
        g.db.rollback(); return api_error('A booking already exists for this examination.')
    _cache_clear('slots:'); _audit('booking_created','booking',cur.lastrowid); return jsonify({'ok':True,'booking_id':cur.lastrowid}),201

@app.get('/api/bookings')
@roles_required('admin','examiner','student')
def bookings_list():
    sql='''SELECT b.id booking_id,b.status booking_status,b.booked_at,s.id slot_id,s.exam_date,s.start_time,s.end_time,e.id examination_id,e.name examination_name,c.code course_code,st.full_name student_name,st.id student_id,ex.full_name examiner_name,ev.total_marks,ev.remarks,ev.status evaluation_status FROM bookings b JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id JOIN courses c ON c.id=e.course_id JOIN users st ON st.id=b.student_id JOIN users ex ON ex.id=s.examiner_id LEFT JOIN evaluations ev ON ev.booking_id=b.id WHERE 1=1'''
    params=[]
    if g.current_user['role']=='student': sql+=' AND b.student_id=?'; params.append(session['user_id'])
    elif g.current_user['role']=='examiner': sql+=' AND s.examiner_id=?'; params.append(session['user_id'])
    if request.args.get('q'): sql+=' AND (st.full_name LIKE ? OR e.name LIKE ?)'; params += [f"%{request.args['q']}%"]*2
    sql+=' ORDER BY s.exam_date DESC,s.start_time'
    return jsonify({'ok':True,'bookings':[dict(r) for r in g.db.execute(sql,params).fetchall()]})

@app.post('/api/student/bookings/<int:booking_id>/cancel')
@roles_required('student')
def booking_cancel(booking_id):
    b=g.db.execute('SELECT b.*,s.id slot_id,s.examination_id,e.booking_end FROM bookings b JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id WHERE b.id=? AND b.student_id=?',(booking_id,session['user_id'])).fetchone()
    if not b or b['status']!='Booked': return api_error('Active booking not found.',404)
    g.db.execute('BEGIN IMMEDIATE')
    current=g.db.execute("SELECT status FROM bookings WHERE id=? AND student_id=?",(booking_id,session['user_id'])).fetchone()
    if not current or current['status']!='Booked': g.db.rollback(); return api_error('Booking is no longer active.')
    if b['booking_end'] and g.db.execute("SELECT datetime('now','localtime') > datetime(?)",(b['booking_end'],)).fetchone()[0]: g.db.rollback(); return api_error('Cancellation deadline has passed.')
    g.db.execute("UPDATE bookings SET status='Cancelled' WHERE id=? AND status='Booked'",(booking_id,)); g.db.execute("UPDATE exam_slots SET available_seats=MIN(max_capacity,available_seats+1),status='Available' WHERE id=? AND status!='Cancelled'",(b['slot_id'],)); g.db.commit(); _cache_clear('slots:'); _audit('booking_cancelled','booking',booking_id); return jsonify({'ok':True})

@app.get('/api/examiner/evaluations')
@roles_required('examiner')
def examiner_evaluations():
    rows=[dict(r) for r in g.db.execute('''SELECT b.id booking_id,st.full_name student_name,st.email,e.id examination_id,e.name examination_name,e.max_marks,s.exam_date,s.start_time,ev.id evaluation_id,ev.total_marks,ev.remarks,ev.status FROM bookings b JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id JOIN users st ON st.id=b.student_id JOIN evaluations ev ON ev.booking_id=b.id WHERE s.examiner_id=? AND b.status='Booked' ORDER BY s.exam_date,s.start_time''',(session['user_id'],)).fetchall()]
    return jsonify({'ok':True,'evaluations':rows})

@app.post('/api/examiner/evaluations/<int:booking_id>')
@roles_required('examiner')
def evaluation_submit(booking_id):
    d=_body(); b=g.db.execute('''SELECT b.*,s.examiner_id,s.examination_id,e.max_marks,e.results_published FROM bookings b JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id WHERE b.id=?''',(booking_id,)).fetchone()
    if not b or b['examiner_id']!=session['user_id'] or b['status']!='Booked': return api_error('You cannot evaluate this booking.',403)
    if b['results_published']: return api_error('Results are already published; evaluation is locked.')
    rubric_rows=g.db.execute('SELECT * FROM rubrics WHERE examination_id=?',(b['examination_id'],)).fetchall()
    scores=d.get('scores',{}); total=0.0
    if rubric_rows:
        for r in rubric_rows:
            try: mark=float(scores.get(str(r['id']),-1))
            except: mark=-1
            if mark<0 or mark>r['max_marks']: return api_error(f"Marks for {r['criterion_name']} must be between 0 and {r['max_marks']}.")
            total+=mark
    else:
        try: total=float(d.get('total_marks',-1))
        except: total=-1
    if total<0 or total>b['max_marks']: return api_error(f"Total marks must be between 0 and {b['max_marks']}.")
    ev=g.db.execute('SELECT id FROM evaluations WHERE booking_id=?',(booking_id,)).fetchone()
    if not ev:
        cur=g.db.execute("INSERT INTO evaluations(booking_id,student_id,examiner_id,status) VALUES(?,?,?,'Pending')",(booking_id,b['student_id'],session['user_id'])); ev={'id':cur.lastrowid}
    g.db.execute("UPDATE evaluations SET total_marks=?,remarks=?,evaluated_at=CURRENT_TIMESTAMP,status='Submitted' WHERE id=?",(total,d.get('remarks',''),ev['id']))
    if rubric_rows:
        g.db.execute('DELETE FROM evaluation_scores WHERE evaluation_id=?',(ev['id'],))
        for r in rubric_rows: g.db.execute('INSERT INTO evaluation_scores(evaluation_id,rubric_id,marks_awarded) VALUES(?,?,?)',(ev['id'],r['id'],float(scores[str(r['id'])])))
    g.db.commit(); _audit('evaluation_submitted','evaluation',ev['id']); return jsonify({'ok':True,'total_marks':total})

@app.post('/api/admin/examinations/<int:exam_id>/publish')
@roles_required('admin')
def publish_results(exam_id):
    if not _exam(exam_id): return api_error('Examination not found.',404)
    pending=g.db.execute("SELECT COUNT(*) n FROM bookings b JOIN exam_slots s ON s.id=b.slot_id LEFT JOIN evaluations v ON v.booking_id=b.id WHERE s.examination_id=? AND b.status='Booked' AND (v.id IS NULL OR v.status!='Submitted')",(exam_id,)).fetchone()['n']
    if pending: return api_error(f'{pending} booked student(s) still have pending evaluations.')
    g.db.execute('INSERT OR IGNORE INTO result_publications(examination_id,published_by) VALUES(?,?)',(exam_id,session['user_id'])); g.db.execute('UPDATE examinations SET results_published=1,status=CASE WHEN status="Completed" THEN status ELSE "Completed" END WHERE id=?',(exam_id,)); g.db.commit(); _audit('results_published','examination',exam_id); return jsonify({'ok':True})

@app.get('/api/admin/reports/summary')
@roles_required('admin')
def reports_summary():
    def count(sql): return g.db.execute(sql).fetchone()[0]
    stats={'courses':count('SELECT COUNT(*) FROM courses'),'examinations':count('SELECT COUNT(*) FROM examinations'),'students':count("SELECT COUNT(*) FROM users WHERE role='student'"),'examiners':count("SELECT COUNT(*) FROM users WHERE role='examiner'"),'slots':count('SELECT COUNT(*) FROM exam_slots'),'bookings':count("SELECT COUNT(*) FROM bookings WHERE status='Booked'"),'evaluated':count("SELECT COUNT(*) FROM evaluations WHERE status='Submitted'"),'pending_evaluations':count("SELECT COUNT(*) FROM evaluations WHERE status='Pending'")}
    active=[dict(r) for r in g.db.execute("SELECT u.full_name,COUNT(b.id) completed FROM users u JOIN exam_slots s ON s.examiner_id=u.id JOIN bookings b ON b.slot_id=s.id AND b.status='Completed' WHERE u.role='examiner' GROUP BY u.id ORDER BY completed DESC LIMIT 5").fetchall()]
    return jsonify({'ok':True,'stats':stats,'active_examiners':active})

@app.post('/api/student/exports')
@roles_required('student')
def export_history():
    try:
        from tasks import export_student_history
        job=export_student_history.delay(session['user_id'])
        return jsonify({'ok':True,'task_id':job.id,'message':'Export queued. Refresh Export jobs in a few seconds.'}),202
    except Exception as exc:
        return api_error('Background worker is unavailable. Start Redis and the Celery worker, then retry.',503)

@app.get('/api/student/exports')
@roles_required('student')
def export_list():
    rows=[dict(r) for r in g.db.execute('SELECT id,status,created_at,completed_at,file_path FROM export_jobs WHERE student_id=? ORDER BY id DESC',(session['user_id'],)).fetchall()]
    for r in rows:
        if r['file_path']: r['download_url']=f"/api/student/exports/{r['id']}/download"
    return jsonify({'ok':True,'jobs':rows})

@app.get('/api/student/exports/<int:job_id>/download')
@roles_required('student')
def export_download(job_id):
    from flask import send_file
    row=g.db.execute('SELECT * FROM export_jobs WHERE id=? AND student_id=?',(job_id,session['user_id'])).fetchone()
    if not row or not row['file_path'] or not os.path.isfile(row['file_path']): return api_error('Export file is not ready.',404)
    return send_file(row['file_path'],as_attachment=True,download_name=f'examination-history-{job_id}.csv',mimetype='text/csv')

@app.get('/api/notifications')
@login_required
def notifications_list():
    rows=[dict(r) for r in g.db.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50',(session['user_id'],)).fetchall()]
    return jsonify({'ok':True,'notifications':rows})

@app.post('/api/admin/examinations/<int:exam_id>/timeline-action')
@roles_required('admin')
def timeline_action(exam_id):
    d=_body(); status=d.get('status')
    if status not in ('Draft','Slot Creation','Booking Open','Closed','Completed'): return api_error('Invalid examination status.')
    ex=_exam(exam_id)
    if not ex: return api_error('Examination not found.',404)
    if status=='Booking Open' and not g.db.execute('SELECT 1 FROM exam_slots WHERE examination_id=? AND status IN (\'Available\',\'Full\')',(exam_id,)).fetchone(): return api_error('Create at least one slot before opening booking.')
    g.db.execute('UPDATE examinations SET status=? WHERE id=?',(status,exam_id)); g.db.commit(); _cache_clear('exams:'); _audit('timeline_changed','examination',exam_id,status); return jsonify({'ok':True})

@app.post('/api/admin/bookings/<int:booking_id>/reschedule')
@roles_required('admin')
def admin_reschedule(booking_id):
    d=_body(); target=g.db.execute('SELECT * FROM exam_slots WHERE id=? AND status="Available" AND available_seats>0',(d.get('slot_id'),)).fetchone()
    old=g.db.execute('SELECT b.*,s.examination_id old_exam FROM bookings b JOIN exam_slots s ON s.id=b.slot_id WHERE b.id=? AND b.status="Booked"',(booking_id,)).fetchone()
    if not old or not target or target['examination_id']!=old['old_exam']: return api_error('Booking or compatible target slot not found.')
    if g.db.execute("SELECT 1 FROM bookings WHERE student_id=? AND slot_id=? AND status='Booked'",(old['student_id'],target['id'])).fetchone(): return api_error('Student already has this slot.')
    g.db.execute('BEGIN IMMEDIATE')
    reserved=g.db.execute("UPDATE exam_slots SET available_seats=available_seats-1,status=CASE WHEN available_seats-1=0 THEN 'Full' ELSE 'Available' END WHERE id=? AND status='Available' AND available_seats>0",(target['id'],)).rowcount
    if not reserved: g.db.rollback(); return api_error('The target slot is no longer available.')
    g.db.execute("UPDATE exam_slots SET available_seats=available_seats+1,status='Available' WHERE id=?",(old['slot_id'],))
    g.db.execute('UPDATE bookings SET slot_id=? WHERE id=?',(target['id'],booking_id)); g.db.execute('UPDATE evaluations SET examiner_id=? WHERE booking_id=?',(target['examiner_id'],booking_id)); g.db.commit(); _audit('booking_rescheduled','booking',booking_id); return jsonify({'ok':True})

@app.post('/api/admin/bookings/<int:booking_id>/reassign')
@roles_required('admin')
def admin_reassign(booking_id):
    d=_body(); examiner_id=d.get('examiner_id')
    b=g.db.execute('SELECT b.*,s.examination_id FROM bookings b JOIN exam_slots s ON s.id=b.slot_id WHERE b.id=? AND b.status="Booked"',(booking_id,)).fetchone()
    if not b or not _examiner_assigned(b['examination_id'],examiner_id): return api_error('Booking or assigned examiner not found.')
    g.db.execute('UPDATE exam_slots SET examiner_id=? WHERE id=(SELECT slot_id FROM bookings WHERE id=?)',(examiner_id,booking_id)); g.db.execute('UPDATE evaluations SET examiner_id=? WHERE booking_id=?',(examiner_id,booking_id)); g.db.commit(); _cache_clear('slots:'); _audit('examiner_reassigned','booking',booking_id,str(examiner_id)); return jsonify({'ok':True})

@app.get('/api/health/details')
def health_details():
    redis_ok=False
    try:
        import redis
        redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0'),socket_connect_timeout=1).ping(); redis_ok=True
    except Exception: pass
    return jsonify({'ok':True,'database':'sqlite','redis':'connected' if redis_ok else 'not connected','celery':'configured' if os.environ.get('REDIS_URL') else 'not configured'})


@app.post('/api/examiner/bookings/<int:booking_id>/complete')
@roles_required('examiner')
def examiner_complete_booking(booking_id):
    row=g.db.execute('''SELECT b.id,b.slot_id FROM bookings b JOIN exam_slots s ON s.id=b.slot_id WHERE b.id=? AND s.examiner_id=? AND b.status='Booked' ''',(booking_id,session['user_id'])).fetchone()
    if not row: return api_error('Booking not found in your slots.',404)
    ev=g.db.execute("SELECT status FROM evaluations WHERE booking_id=?",(booking_id,)).fetchone()
    if not ev or ev['status']!='Submitted': return api_error('Submit the evaluation before completing this booking.')
    g.db.execute("UPDATE bookings SET status='Completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",(booking_id,))
    remaining=g.db.execute("SELECT COUNT(*) FROM bookings WHERE slot_id=? AND status='Booked'",(row['slot_id'],)).fetchone()[0]
    if remaining==0: g.db.execute("UPDATE exam_slots SET status='Completed' WHERE id=?",(row['slot_id'],))
    g.db.commit(); _audit('booking_completed','booking',booking_id); return jsonify({'ok':True})

@app.get('/api/student/results')
@roles_required('student')
def student_results():
    rows=[dict(r) for r in g.db.execute('''SELECT e.name examination_name,c.code course_code,s.exam_date,ex.full_name examiner_name,v.total_marks,v.remarks,v.evaluated_at FROM evaluations v JOIN bookings b ON b.id=v.booking_id JOIN exam_slots s ON s.id=b.slot_id JOIN examinations e ON e.id=s.examination_id JOIN courses c ON c.id=e.course_id JOIN users ex ON ex.id=v.examiner_id WHERE v.student_id=? AND v.status='Submitted' AND e.results_published=1 ORDER BY s.exam_date DESC''',(session['user_id'],)).fetchall()]
    return jsonify({'ok':True,'results':rows})


@app.post('/api/examiner/examinations/<int:exam_id>/complete')
@roles_required('examiner')
def examiner_complete_examination(exam_id):
    if not _examiner_assigned(exam_id,session['user_id']): return api_error('You are not assigned to this examination.',403)
    pending=g.db.execute("SELECT COUNT(*) FROM bookings b JOIN exam_slots s ON s.id=b.slot_id LEFT JOIN evaluations v ON v.booking_id=b.id WHERE s.examination_id=? AND b.status='Booked' AND (v.id IS NULL OR v.status!='Submitted')",(exam_id,)).fetchone()[0]
    if pending: return api_error(f'{pending} booking(s) still need evaluation.')
    g.db.execute("UPDATE examinations SET status='Completed' WHERE id=?",(exam_id,)); g.db.commit(); _audit('examiner_marked_exam_completed','examination',exam_id); return jsonify({'ok':True})

@app.errorhandler(413)
def request_too_large(_error):
    return api_error("Request body is too large.", 413)


@app.errorhandler(500)
def server_error(_error):
    return api_error("An unexpected server error occurred.", 500)


if __name__ == "__main__":
    init_db()
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("EMP_DEBUG", "1") == "1",
    )
