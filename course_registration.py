#!/usr/bin/env python3
"""
Course Registration System - an OBS-inspired course registration app.

Run:    python course_registration.py
Reset:  python course_registration.py --reset     (rebuilds the demo database)
Info:   python course_registration.py --version

Needs only Python 3.9+ (Tkinter and SQLite come with the standard installer).
Tip: you can open the app twice (two terminals) and log in as different users;
notifications show up live in the other window within a few seconds.

Demo accounts
  Students : 20260001 ... 20260008  / 1234
  Advisors : A1001, A1002           / 1234
  Admin    : admin                  / admin

Registration problems handled
  - time conflicts, exam clashes (warning), missing prerequisites
  - full classes -> waitlist with automatic promotion + banner notification
  - AKTS load limit (30, or 36 for students with CGPA >= 3.00)
  - duplicate registration / retaking a passed course
  - registration window (open/closed) and per-semester offerings
  - advisor approval workflow: Draft -> Pending -> Approved
  - advisors can switch on automatic capacity growth: when a waitlist reaches N students,
    the course gets extra seats (up to a maximum) and the waitlisted students move in
"""
import hashlib
import os
import random
import sqlite3
import sys
from datetime import datetime

__version__ = "1.0.0"

if "--version" in sys.argv:  # works even where Tkinter is not installed
    print(f"Course Registration System {__version__}")
    sys.exit(0)

if sys.platform == "win32":  # crisp text on high-DPI Windows screens (otherwise Windows stretches and blurs the window)
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk
except ImportError:  # pragma: no cover
    print("Tkinter is missing. On Linux run:  sudo apt install python3-tk")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registration.db")

TERMS = ["2025-26 Fall", "2025-26 Spring", "2026-27 Fall", "2026-27 Spring"]
START_TERM = "2026-27 Fall"
BASE_CREDIT_LIMIT = 30
HIGH_GPA_LIMIT = 36
HIGH_GPA_THRESHOLD = 3.0
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
HOURS = list(range(8, 18))
GRADE_POINTS = {"AA": 4.0, "BA": 3.5, "BB": 3.0, "CB": 2.5, "CC": 2.0, "DC": 1.5, "DD": 1.0, "FD": 0.5, "FF": 0.0}
FAIL_GRADES = {"FD", "FF"}
GRADE_WEIGHTS = [("AA", 10), ("BA", 15), ("BB", 20), ("CB", 20), ("CC", 15), ("DC", 8), ("DD", 5), ("FD", 3), ("FF", 4)]

# ==========================================================================
# DATA LAYER (no UI code - easy to test on its own)
# ==========================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, department TEXT,
    advisor_id TEXT, salt TEXT NOT NULL, pw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS courses(
    code TEXT PRIMARY KEY, name TEXT NOT NULL, credits INTEGER NOT NULL, level INTEGER NOT NULL,
    term_type TEXT NOT NULL, prereq TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS offerings(
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, term TEXT NOT NULL, instructor TEXT,
    room TEXT, capacity INTEGER NOT NULL, exam_date TEXT, exam_hour INTEGER, UNIQUE(code, term));
CREATE TABLE IF NOT EXISTS slots(
    offering_id INTEGER NOT NULL, day INTEGER NOT NULL, start_hour INTEGER NOT NULL, end_hour INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments(
    student_id TEXT NOT NULL, offering_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Draft',
    created TEXT, PRIMARY KEY(student_id, offering_id));
CREATE TABLE IF NOT EXISTS waitlist(
    id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, offering_id INTEGER NOT NULL,
    UNIQUE(student_id, offering_id));
CREATE TABLE IF NOT EXISTS completed(
    student_id TEXT NOT NULL, code TEXT NOT NULL, term TEXT NOT NULL, grade TEXT NOT NULL,
    PRIMARY KEY(student_id, code, term));
CREATE TABLE IF NOT EXISTS notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, message TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'info', created TEXT, seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS capacity_rules(
    offering_id INTEGER PRIMARY KEY, advisor_id TEXT NOT NULL, threshold INTEGER NOT NULL,
    increase INTEGER NOT NULL, max_capacity INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created TEXT);
CREATE TABLE IF NOT EXISTS announcements(
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT, created TEXT);
"""

# (code, name, credits, curriculum semester, prerequisite, description)
SEED_COURSES = [
    ("CSE101", "Introduction to Programming", 6, 1, None, "Variables, control flow, functions and basic data structures in Python."),
    ("MAT101", "Calculus I", 6, 1, None, "Limits, derivatives and integrals of single-variable functions."),
    ("PHY101", "Physics I", 5, 1, None, "Mechanics, work and energy, oscillations."),
    ("ENG101", "Academic English I", 3, 1, None, "Reading and writing skills for university study."),
    ("HUM101", "Introduction to Philosophy", 3, 1, None, "Major questions and thinkers in Western philosophy."),
    ("CSE102", "Data Structures", 6, 2, "CSE101", "Lists, stacks, queues, trees, hash tables and graphs."),
    ("MAT102", "Calculus II", 6, 2, "MAT101", "Sequences, series and multivariable calculus."),
    ("PHY102", "Physics II", 5, 2, "PHY101", "Electricity, magnetism and waves."),
    ("ENG102", "Academic English II", 3, 2, "ENG101", "Academic writing and presentations."),
    ("CSE104", "Discrete Mathematics", 5, 2, None, "Logic, sets, relations, counting and graph theory."),
    ("CSE201", "Algorithms", 6, 3, "CSE102", "Algorithm design and analysis."),
    ("CSE203", "Computer Organization", 5, 3, None, "Digital logic, processor design and memory."),
    ("MAT201", "Linear Algebra", 5, 3, "MAT102", "Vector spaces, matrices and eigenvalues."),
    ("AIT301", "AI Tools in Practice", 5, 3, None, "Hands-on use of modern AI tools for study and work."),
    ("DBS210", "Databases", 6, 4, "CSE102", "Relational model, SQL and database design."),
    ("CSE204", "Operating Systems", 6, 4, "CSE203", "Processes, memory management and file systems."),
]
# (code, term, instructor, room, capacity, exam_date, exam_hour, [(day, start, end), ...])  day: 0=Mon
SEED_OFFERINGS = [
    ("CSE101", "2026-27 Fall", "Dr. Aydin", "B-101", 3, "2027-01-11", 10, [(0, 9, 11), (2, 9, 10)]),
    ("MAT101", "2026-27 Fall", "Dr. Polat", "A-201", 40, "2027-01-12", 10, [(1, 9, 11), (3, 9, 10)]),
    ("PHY101", "2026-27 Fall", "Dr. Celik", "LAB-2", 40, "2027-01-12", 10, [(2, 14, 17)]),
    ("ENG101", "2026-27 Fall", "Ms. Kurt", "C-105", 30, "2027-01-13", 14, [(4, 9, 12)]),
    ("HUM101", "2026-27 Fall", "Dr. Tan", "A-104", 2, "2027-01-14", 10, [(1, 14, 17)]),
    ("CSE201", "2026-27 Fall", "Dr. Sahin", "B-203", 30, "2027-01-15", 13, [(0, 13, 15), (2, 13, 14)]),
    ("CSE203", "2026-27 Fall", "Dr. Sahin", "B-204", 30, "2027-01-18", 10, [(1, 10, 12), (3, 10, 11)]),
    ("MAT201", "2026-27 Fall", "Dr. Polat", "A-202", 30, "2027-01-19", 10, [(0, 11, 13), (2, 11, 12)]),
    ("AIT301", "2026-27 Fall", "Dr. Ozkan", "LAB-1", 25, "2027-01-20", 14, [(4, 10, 13)]),
    ("CSE102", "2026-27 Spring", "Dr. Aydin", "B-101", 30, "2027-06-08", 10, [(1, 10, 12), (3, 10, 11)]),
    ("MAT102", "2026-27 Spring", "Dr. Polat", "A-201", 40, "2027-06-09", 10, [(0, 11, 13), (2, 11, 12)]),
    ("PHY102", "2026-27 Spring", "Dr. Celik", "LAB-2", 40, "2027-06-09", 10, [(2, 14, 17)]),
    ("ENG102", "2026-27 Spring", "Ms. Kurt", "C-105", 30, "2027-06-10", 14, [(4, 9, 12)]),
    ("CSE104", "2026-27 Spring", "Dr. Tan", "B-102", 30, "2027-06-11", 10, [(0, 9, 11), (2, 9, 10)]),
    ("DBS210", "2026-27 Spring", "Dr. Sahin", "B-203", 30, "2027-06-14", 13, [(3, 13, 16)]),
    ("CSE204", "2026-27 Spring", "Dr. Sahin", "B-204", 30, "2027-06-15", 10, [(1, 13, 15), (3, 13, 14)]),
]
# (id, name, role, advisor_id, password)
SEED_USERS = [
    ("admin", "Registrar Office", "admin", None, "admin"),
    ("A1001", "Dr. Aydin", "advisor", None, "1234"),
    ("A1002", "Dr. Sahin", "advisor", None, "1234"),
    ("20260001", "Ayse Yilmaz", "student", "A1001", "1234"),
    ("20260002", "Mehmet Demir", "student", "A1001", "1234"),
    ("20260003", "Elif Kaya", "student", "A1002", "1234"),
    ("20260004", "Can Oz", "student", "A1002", "1234"),
    ("20260005", "Zeynep Arslan", "student", "A1001", "1234"),
    ("20260006", "Burak Kara", "student", "A1002", "1234"),
    ("20260007", "Selin Acar", "student", "A1001", "1234"),
    ("20260008", "Deniz Eren", "student", "A1002", "1234"),
]
F, S = "2025-26 Fall", "2025-26 Spring"
SEED_COMPLETED = [
    ("20260001", "CSE101", F, "AA"), ("20260001", "MAT101", F, "BA"), ("20260001", "PHY101", F, "BB"),
    ("20260001", "ENG101", F, "AA"), ("20260001", "HUM101", F, "BA"),
    ("20260001", "CSE102", S, "BA"), ("20260001", "MAT102", S, "BB"), ("20260001", "PHY102", S, "AA"),
    ("20260001", "ENG102", S, "AA"), ("20260001", "CSE104", S, "BB"),
    ("20260003", "CSE101", F, "FF"), ("20260003", "MAT101", F, "CB"), ("20260003", "PHY101", F, "CC"),
    ("20260003", "ENG101", F, "BB"), ("20260003", "HUM101", F, "BB"),
    ("20260003", "MAT102", S, "CC"), ("20260003", "ENG102", S, "BB"), ("20260003", "CSE104", S, "CC"),
    ("20260004", "CSE101", F, "CC"), ("20260004", "MAT101", F, "CC"), ("20260004", "PHY101", F, "DD"),
    ("20260004", "ENG101", F, "CB"), ("20260004", "HUM101", F, "CB"),
    ("20260004", "CSE102", S, "DC"), ("20260004", "MAT102", S, "FF"), ("20260004", "ENG102", S, "CC"),
    ("20260004", "CSE104", S, "CB"),
]
# students who already hold a seat (so CSE101 starts 2/3 and HUM101 starts 2/2 = full)
SEED_ENROLLED = [("20260005", "CSE101"), ("20260005", "HUM101"), ("20260006", "CSE101"), ("20260006", "HUM101")]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 20000).hex()


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 0:
        seed(conn)
    conn.close()


def seed(conn):
    for code, name, cr, level, pre, desc in SEED_COURSES:
        conn.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?)",
                     (code, name, cr, level, "Fall" if level % 2 else "Spring", pre, desc))
    ids = {}
    for code, term, inst, room, cap, ed, eh, slots in SEED_OFFERINGS:
        cur = conn.execute(
            "INSERT INTO offerings(code,term,instructor,room,capacity,exam_date,exam_hour) VALUES (?,?,?,?,?,?,?)",
            (code, term, inst, room, cap, ed, eh))
        ids[(code, term)] = cur.lastrowid
        for d, s, e in slots:
            conn.execute("INSERT INTO slots VALUES (?,?,?,?)", (cur.lastrowid, d, s, e))
    for uid, name, role, adv, pw in SEED_USERS:
        salt = os.urandom(8).hex()
        conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                     (uid, name, role, "Computer Engineering" if role == "student" else None, adv, salt, hash_pw(pw, salt)))
    conn.executemany("INSERT INTO completed VALUES (?,?,?,?)", SEED_COMPLETED)
    for sid, code in SEED_ENROLLED:
        conn.execute("INSERT INTO enrollments VALUES (?,?,?,?)", (sid, ids[(code, START_TERM)], "Approved", now()))
    set_setting(conn, "current_term", START_TERM)
    set_setting(conn, "reg_open", "1")
    conn.execute("INSERT INTO announcements(title, body, created) VALUES (?,?,?)",
                 ("Registration is open", f"Course registration for {START_TERM} is open. Add your courses and submit them to your advisor.", now()))
    conn.execute("INSERT INTO announcements(title, body, created) VALUES (?,?,?)",
                 ("AKTS load", f"Students may take up to {BASE_CREDIT_LIMIT} AKTS per term ({HIGH_GPA_LIMIT} with a CGPA of {HIGH_GPA_THRESHOLD:.2f} or higher).", now()))
    for u in conn.execute("SELECT id FROM users WHERE role='student'").fetchall():
        notify(conn, u["id"], f"Welcome! Registration for {START_TERM} is open. Pick your courses and send them to your advisor.", "info")
    conn.commit()


# ---------- settings, users, notifications ----------
def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))


def reg_is_open(conn):
    return get_setting(conn, "reg_open") == "1"


def authenticate(conn, uid, password):
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid.strip(),)).fetchone()
    if u and hash_pw(password, u["salt"]) == u["pw_hash"]:
        return u
    return None


def change_password(conn, uid, old, new, confirm):
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if hash_pw(old, u["salt"]) != u["pw_hash"]:
        return False, "Current password is wrong."
    if len(new) < 4:
        return False, "New password must be at least 4 characters."
    if new != confirm:
        return False, "New passwords do not match."
    salt = os.urandom(8).hex()
    conn.execute("UPDATE users SET salt=?, pw_hash=? WHERE id=?", (salt, hash_pw(new, salt), uid))
    conn.commit()
    return True, "Password changed."


def user_name(conn, uid):
    r = conn.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
    return r["name"] if r else uid


def notify(conn, uid, message, kind="info"):
    conn.execute("INSERT INTO notifications(user_id, message, kind, created) VALUES (?,?,?,?)", (uid, message, kind, now()))


def unseen_notifications(conn, uid):
    return conn.execute("SELECT * FROM notifications WHERE user_id=? AND seen=0 ORDER BY id", (uid,)).fetchall()


def all_notifications(conn, uid):
    return conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()


def mark_seen(conn, nid):
    conn.execute("UPDATE notifications SET seen=1 WHERE id=?", (nid,))
    conn.commit()


def mark_all_seen(conn, uid):
    conn.execute("UPDATE notifications SET seen=1 WHERE user_id=?", (uid,))
    conn.commit()


def get_announcements(conn, limit=20):
    return conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def add_announcement(conn, title, body):
    conn.execute("INSERT INTO announcements(title, body, created) VALUES (?,?,?)", (title, body, now()))
    for u in conn.execute("SELECT id FROM users WHERE role='student'").fetchall():
        notify(conn, u["id"], f"New announcement: {title}", "info")
    conn.commit()


# ---------- offerings & schedule ----------
OFF_COLS = ("o.id, o.code, o.term, o.instructor, o.room, o.capacity, o.exam_date, o.exam_hour, "
            "c.name, c.credits, c.level, c.term_type, c.prereq, c.description")
OFF_FROM = "offerings o JOIN courses c ON c.code=o.code"


def offering(conn, oid):
    return conn.execute(f"SELECT {OFF_COLS} FROM {OFF_FROM} WHERE o.id=?", (oid,)).fetchone()


def terms_with_offerings(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT term FROM offerings ORDER BY term").fetchall()]


def list_offerings(conn, term=None, level=None, search=""):
    sql, args = f"SELECT {OFF_COLS} FROM {OFF_FROM} WHERE 1=1", []
    if term:
        sql += " AND o.term=?"
        args.append(term)
    if level:
        sql += " AND c.level=?"
        args.append(level)
    if search:
        sql += " AND (o.code LIKE ? OR c.name LIKE ? OR o.instructor LIKE ?)"
        args += [f"%{search}%"] * 3
    return conn.execute(sql + " ORDER BY o.term, c.level, o.code", args).fetchall()


def offering_slots(conn, oid):
    return conn.execute("SELECT day, start_hour, end_hour FROM slots WHERE offering_id=? ORDER BY day, start_hour", (oid,)).fetchall()


def format_slots(conn, oid):
    return ", ".join(f"{DAYS[s['day']]} {s['start_hour']:02d}-{s['end_hour']:02d}" for s in offering_slots(conn, oid))


def format_exam(o):
    return f"{o['exam_date']} {o['exam_hour']:02d}:00" if o["exam_date"] else "TBA"


def slots_overlap(a, b):
    return any(x["day"] == y["day"] and x["start_hour"] < y["end_hour"] and y["start_hour"] < x["end_hour"] for x in a for y in b)


def seats_taken(conn, oid):
    return conn.execute("SELECT COUNT(*) FROM enrollments WHERE offering_id=?", (oid,)).fetchone()[0]


def waitlist_len(conn, oid):
    return conn.execute("SELECT COUNT(*) FROM waitlist WHERE offering_id=?", (oid,)).fetchone()[0]


# ---------- transcript / GPA ----------
def transcript_rows(conn, sid):
    return conn.execute(
        "SELECT t.term, t.code, c.name, c.credits, t.grade FROM completed t JOIN courses c ON c.code=t.code "
        "WHERE t.student_id=? ORDER BY t.term, t.code", (sid,)).fetchall()


def latest_attempts(rows):
    latest = {}
    for r in rows:  # rows are ordered by term, so the last one wins
        latest[r["code"]] = r
    return latest


def calc_gpa(rows):
    credits = sum(r["credits"] for r in rows)
    if not credits:
        return None
    return sum(GRADE_POINTS[r["grade"]] * r["credits"] for r in rows) / credits


def cgpa(rows):
    return calc_gpa(list(latest_attempts(rows).values()))


def passed_set(rows):
    return {code for code, r in latest_attempts(rows).items() if r["grade"] not in FAIL_GRADES}


def earned_credits(rows):
    return sum(r["credits"] for r in latest_attempts(rows).values() if r["grade"] not in FAIL_GRADES)


def credit_limit_from_rows(rows):
    g = cgpa(rows)
    return HIGH_GPA_LIMIT if g is not None and g >= HIGH_GPA_THRESHOLD else BASE_CREDIT_LIMIT


# ---------- a student's registrations ----------
def my_regs(conn, sid, term=None):
    term = term or get_setting(conn, "current_term")
    return conn.execute(
        f"SELECT {OFF_COLS}, e.status FROM enrollments e JOIN offerings o ON o.id=e.offering_id "
        "JOIN courses c ON c.code=o.code WHERE e.student_id=? AND o.term=? ORDER BY o.code", (sid, term)).fetchall()


def my_waitlist(conn, sid, term=None):
    """[(offering_row, position), ...]"""
    term = term or get_setting(conn, "current_term")
    rows = conn.execute(
        f"SELECT {OFF_COLS}, w.id AS wid FROM waitlist w JOIN offerings o ON o.id=w.offering_id "
        "JOIN courses c ON c.code=o.code WHERE w.student_id=? AND o.term=? ORDER BY o.code", (sid, term)).fetchall()
    out = []
    for r in rows:
        pos = conn.execute("SELECT COUNT(*) FROM waitlist WHERE offering_id=? AND id<=?", (r["id"], r["wid"])).fetchone()[0]
        out.append((r, pos))
    return out


def reg_credits(conn, sid):
    return sum(r["credits"] for r in my_regs(conn, sid))


def enrollment_status(conn, sid, oid):
    r = conn.execute("SELECT status FROM enrollments WHERE student_id=? AND offering_id=?", (sid, oid)).fetchone()
    return r["status"] if r else None


def on_waitlist(conn, sid, oid):
    return conn.execute("SELECT 1 FROM waitlist WHERE student_id=? AND offering_id=?", (sid, oid)).fetchone() is not None


def status_label(conn, sid, o, passed=None):
    st = enrollment_status(conn, sid, o["id"])
    if st:
        return st
    if on_waitlist(conn, sid, o["id"]):
        return "Waitlisted"
    if passed is None:
        passed = passed_set(transcript_rows(conn, sid))
    if o["code"] in passed:
        return "Passed"
    if o["term"] != get_setting(conn, "current_term"):
        return "Closed"
    return "Full" if seats_taken(conn, o["id"]) >= o["capacity"] else "Open"


# ---------- registration rules ----------
def check_eligibility(conn, sid, oid, ignore_window=False):
    """None if the student may take this offering, otherwise the reason why not."""
    o = offering(conn, oid)
    if o is None:
        return "Course offering not found."
    cur = get_setting(conn, "current_term")
    if o["term"] != cur:
        return f"Registration is only open for {cur} courses."
    if not ignore_window and not reg_is_open(conn):
        return "The registration period is closed. Please contact the registrar."
    if conn.execute("SELECT 1 FROM enrollments e JOIN offerings x ON x.id=e.offering_id "
                    "WHERE e.student_id=? AND x.code=? AND x.term=?", (sid, o["code"], cur)).fetchone():
        return "You are already registered for this course."
    rows = transcript_rows(conn, sid)
    passed = passed_set(rows)
    if o["code"] in passed:
        return "You have already passed this course."
    if o["prereq"] and o["prereq"] not in passed:
        return f"Prerequisite not met: you must pass {o['prereq']} first."
    new_slots = offering_slots(conn, oid)
    for r in my_regs(conn, sid):
        if slots_overlap(new_slots, offering_slots(conn, r["id"])):
            return f"Time conflict with {r['code']} ({r['name']})."
    limit = credit_limit_from_rows(rows)
    total = reg_credits(conn, sid) + o["credits"]
    if total > limit:
        return f"AKTS limit exceeded: this would make {total} AKTS (your limit is {limit})."
    return None


def exam_clashes(conn, sid, oid):
    o = offering(conn, oid)
    if not o["exam_date"]:
        return []
    return [r["code"] for r in my_regs(conn, sid)
            if r["id"] != oid and r["exam_date"] == o["exam_date"] and r["exam_hour"] == o["exam_hour"]]


def register(conn, sid, oid):
    """-> (status, message, warnings). status is 'ok', 'full' or 'error'."""
    reason = check_eligibility(conn, sid, oid)
    if reason:
        return "error", reason, []
    o = offering(conn, oid)
    if seats_taken(conn, oid) >= o["capacity"]:
        if on_waitlist(conn, sid, oid):
            return "error", "You are already on the waitlist for this course.", []
        return "full", f"{o['code']} is full ({o['capacity']}/{o['capacity']}).", []
    conn.execute("INSERT INTO enrollments VALUES (?,?,?,?)", (sid, oid, "Draft", now()))
    conn.execute("DELETE FROM waitlist WHERE student_id=? AND offering_id=?", (sid, oid))
    conn.commit()
    warns = []
    clash = exam_clashes(conn, sid, oid)
    if clash:
        warns.append(f"Exam clash: {o['code']} and {', '.join(clash)} are on {format_exam(o)}.")
    return "ok", f"Added {o['code']} - {o['name']} as a draft. Submit it for advisor approval when ready.", warns


def join_waitlist(conn, sid, oid):
    conn.execute("INSERT OR IGNORE INTO waitlist(student_id, offering_id) VALUES (?,?)", (sid, oid))
    conn.commit()
    o = offering(conn, oid)
    grew = apply_capacity_rule(conn, oid)
    if grew and enrollment_status(conn, sid, oid):
        return (f"Seats were added automatically ({grew['from']} -> {grew['to']}), so you were enrolled in {o['code']} as a draft. "
                "Submit it for advisor approval.")
    pos = dict((r["id"], p) for r, p in my_waitlist(conn, sid)).get(oid, 1)
    return f"You are number {pos} on the waitlist for {o['code']}. You will be enrolled automatically if a seat opens."


def leave_waitlist(conn, sid, oid):
    conn.execute("DELETE FROM waitlist WHERE student_id=? AND offering_id=?", (sid, oid))
    conn.commit()


def promote_waitlist(conn, oid, notify_blocked=True):
    """Fill free seats from the waitlist, first come first served. Returns how many were promoted."""
    o = offering(conn, oid)
    promoted = 0
    for w in conn.execute("SELECT * FROM waitlist WHERE offering_id=? ORDER BY id", (oid,)).fetchall():
        if seats_taken(conn, oid) >= o["capacity"]:
            break
        reason = check_eligibility(conn, w["student_id"], oid, ignore_window=True)
        if reason is None:
            conn.execute("INSERT INTO enrollments VALUES (?,?,?,?)", (w["student_id"], oid, "Draft", now()))
            conn.execute("DELETE FROM waitlist WHERE id=?", (w["id"],))
            notify(conn, w["student_id"],
                   f"Good news! A seat opened up and you were moved from the waitlist into {o['code']} - {o['name']}. "
                   "It was added as a draft: submit it for advisor approval.", "success")
            promoted += 1
        elif notify_blocked:
            notify(conn, w["student_id"],
                   f"A seat opened in {o['code']} but you could not be enrolled: {reason}", "warning")
    conn.commit()
    return promoted


def set_capacity(conn, oid, capacity, notify_blocked=True):
    conn.execute("UPDATE offerings SET capacity=? WHERE id=?", (capacity, oid))
    conn.commit()
    return promote_waitlist(conn, oid, notify_blocked)


# ---------- automatic capacity growth (advisor rules) ----------
def get_rule(conn, oid):
    return conn.execute("SELECT * FROM capacity_rules WHERE offering_id=?", (oid,)).fetchone()


def rule_text(r):
    if r is None:
        return "-"
    return f"At {r['threshold']}+ waiting add {r['increase']} seat(s), max {r['max_capacity']}" + ("" if r["enabled"] else "   [off]")


def eligible_waiters(conn, oid):
    """Waitlisted students who could really take a seat (no conflict, prerequisite or load problem)."""
    return sum(1 for w in conn.execute("SELECT student_id FROM waitlist WHERE offering_id=?", (oid,)).fetchall()
               if check_eligibility(conn, w["student_id"], oid, ignore_window=True) is None)


def apply_capacity_rule(conn, oid):
    """If the course has an active rule and its waitlist reached the threshold, add seats (up to the maximum)
    and enrol waitlisted students. Returns {'code','from','to','promoted'} or None when nothing changed."""
    r = get_rule(conn, oid)
    if r is None or not r["enabled"]:
        return None
    o = offering(conn, oid)
    start = cap = o["capacity"]
    promoted_total = 0
    while cap < r["max_capacity"] and waitlist_len(conn, oid) >= r["threshold"] and eligible_waiters(conn, oid) > 0:
        cap = min(r["max_capacity"], cap + r["increase"])
        promoted = set_capacity(conn, oid, cap, notify_blocked=False)
        promoted_total += promoted
        if promoted == 0:
            break
    if cap == start:
        return None
    msg = (f"Auto-capacity: {o['code']} grew from {start} to {cap} seats because its waitlist reached {r['threshold']}. "
           f"{promoted_total} student(s) moved in from the waitlist.")
    if cap >= r["max_capacity"] and waitlist_len(conn, oid) >= r["threshold"]:
        msg += f" The maximum of {r['max_capacity']} seats is reached; {waitlist_len(conn, oid)} student(s) are still waiting."
    notify(conn, r["advisor_id"], msg, "info")
    conn.commit()
    return {"code": o["code"], "from": start, "to": cap, "promoted": promoted_total}


def set_rule(conn, advisor_id, oid, threshold, increase, max_capacity):
    o = offering(conn, oid)
    if threshold < 1 or increase < 1:
        return False, "Waitlist size and seats to add must be at least 1."
    if max_capacity <= o["capacity"]:
        return False, f"Maximum capacity must be higher than the current capacity ({o['capacity']})."
    conn.execute("INSERT OR REPLACE INTO capacity_rules VALUES (?,?,?,?,?,1,?)",
                 (oid, advisor_id, threshold, increase, max_capacity, now()))
    conn.commit()
    res = apply_capacity_rule(conn, oid)
    msg = f"Rule saved for {o['code']}."
    if res:
        msg += f" It fired right away: capacity {res['from']} -> {res['to']}, {res['promoted']} student(s) enrolled."
    return True, msg


def toggle_rule(conn, advisor_id, oid):
    r = get_rule(conn, oid)
    if r is None:
        return False, "This course has no rule yet."
    on = 0 if r["enabled"] else 1
    conn.execute("UPDATE capacity_rules SET enabled=?, advisor_id=? WHERE offering_id=?", (on, advisor_id, oid))
    conn.commit()
    msg = "Rule switched on." if on else "Rule switched off."
    res = apply_capacity_rule(conn, oid) if on else None
    if res:
        msg += f" It fired right away: capacity {res['from']} -> {res['to']}."
    return True, msg


def delete_rule(conn, oid):
    n = conn.execute("DELETE FROM capacity_rules WHERE offering_id=?", (oid,)).rowcount
    conn.commit()
    return n


def submit_registration(conn, sid):
    if not reg_is_open(conn):
        return "error", "The registration period is closed."
    cur = get_setting(conn, "current_term")
    n = conn.execute("UPDATE enrollments SET status='Pending' WHERE student_id=? AND status='Draft' "
                     "AND offering_id IN (SELECT id FROM offerings WHERE term=?)", (sid, cur)).rowcount
    if n == 0:
        return "error", "You have no draft courses to submit."
    adv = conn.execute("SELECT advisor_id FROM users WHERE id=?", (sid,)).fetchone()["advisor_id"]
    if adv:
        notify(conn, adv, f"{user_name(conn, sid)} ({sid}) submitted {n} course(s) for your approval.", "info")
    conn.commit()
    return "ok", f"Submitted {n} course(s) to your advisor for approval."


def pending_for_advisor(conn, aid):
    return conn.execute(
        "SELECT e.student_id, u.name AS sname, e.offering_id, o.code, c.name, c.credits, o.term "
        "FROM enrollments e JOIN users u ON u.id=e.student_id JOIN offerings o ON o.id=e.offering_id "
        "JOIN courses c ON c.code=o.code WHERE u.advisor_id=? AND e.status='Pending' ORDER BY u.id, o.code", (aid,)).fetchall()


def approve(conn, advisor_id, sid, oid):
    n = conn.execute("UPDATE enrollments SET status='Approved' WHERE student_id=? AND offering_id=? AND status='Pending'",
                     (sid, oid)).rowcount
    if n:
        o = offering(conn, oid)
        notify(conn, sid, f"{user_name(conn, advisor_id)} approved your registration for {o['code']}.", "success")
    conn.commit()
    return n


def reject(conn, advisor_id, sid, oid, reason):
    n = conn.execute("DELETE FROM enrollments WHERE student_id=? AND offering_id=? AND status='Pending'", (sid, oid)).rowcount
    if n:
        o = offering(conn, oid)
        notify(conn, sid, f"{user_name(conn, advisor_id)} rejected {o['code']}: {reason or 'no reason given'}. "
                          "Pick another course and submit again.", "error")
        conn.commit()
        promote_waitlist(conn, oid)
    conn.commit()
    return n


def drop(conn, sid, oid):
    if not reg_is_open(conn):
        return "error", "The registration period is closed, so courses cannot be dropped."
    if enrollment_status(conn, sid, oid) is None:
        return "error", "You are not registered for this course."
    conn.execute("DELETE FROM enrollments WHERE student_id=? AND offering_id=?", (sid, oid))
    conn.commit()
    o = offering(conn, oid)
    promote_waitlist(conn, oid)
    return "ok", f"Dropped {o['code']}."


def admin_remove(conn, sid, oid):
    o = offering(conn, oid)
    n = conn.execute("DELETE FROM enrollments WHERE student_id=? AND offering_id=?", (sid, oid)).rowcount
    if n:
        notify(conn, sid, f"The registrar removed you from {o['code']} - {o['name']}.", "warning")
        conn.commit()
        promote_waitlist(conn, oid)
    return n


def roster(conn, oid):
    return conn.execute("SELECT u.id, u.name, e.status FROM enrollments e JOIN users u ON u.id=e.student_id "
                        "WHERE e.offering_id=? ORDER BY u.id", (oid,)).fetchall()


def advisees(conn, aid):
    return conn.execute("SELECT * FROM users WHERE role='student' AND advisor_id=? ORDER BY id", (aid,)).fetchall()


def close_term(conn, rng=random):
    """Demo: approved courses get random grades, then the next term starts."""
    cur = get_setting(conn, "current_term")
    i = TERMS.index(cur)
    if i + 1 >= len(TERMS):
        return False, "There is no next term in this demo."
    nxt = TERMS[i + 1]
    grades, weights = zip(*GRADE_WEIGHTS)
    rows = conn.execute("SELECT e.student_id, o.code FROM enrollments e JOIN offerings o ON o.id=e.offering_id "
                        "WHERE o.term=? AND e.status='Approved'", (cur,)).fetchall()
    for r in rows:
        conn.execute("INSERT OR REPLACE INTO completed VALUES (?,?,?,?)", (r["student_id"], r["code"], cur, rng.choices(grades, weights)[0]))
    conn.execute("DELETE FROM enrollments WHERE offering_id IN (SELECT id FROM offerings WHERE term=?)", (cur,))
    conn.execute("DELETE FROM waitlist WHERE offering_id IN (SELECT id FROM offerings WHERE term=?)", (cur,))
    set_setting(conn, "current_term", nxt)
    set_setting(conn, "reg_open", "1")
    for u in conn.execute("SELECT id FROM users WHERE role='student'").fetchall():
        notify(conn, u["id"], f"{cur} grades are posted. Registration for {nxt} is now open.", "info")
    conn.commit()
    return True, f"Term closed. {len(rows)} grades posted. Current term is now {nxt}."


# ==========================================================================
# USER INTERFACE
# ==========================================================================
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")  # optional: drop your own logo here

UI_SCALE = 1.0  # set at startup from the screen DPI so the layout stays proportional on high-DPI displays


def px(n):
    return int(round(n * UI_SCALE))

SIDEBAR, SIDEBAR_HOVER, SIDEBAR_ACTIVE = "#0f1b2d", "#1a2a44", "#223758"
ACCENT, ACCENT_DARK = "#2563eb", "#1d4ed8"
BG, CARD, BORDER, TEXT, MUTED = "#f3f5f9", "#ffffff", "#e5e7eb", "#1f2937", "#6b7280"
CHIP = {  # name: (background, text)
    "Approved": ("#dcfce7", "#166534"), "Pending": ("#dbeafe", "#1e40af"), "Draft": ("#fef3c7", "#92400e"),
    "Waitlisted": ("#ffedd5", "#9a3412"), "Full": ("#fee2e2", "#991b1b"), "Open": ("#e0f2fe", "#075985"),
    "Passed": ("#f3f4f6", "#4b5563"), "Closed": ("#f3f4f6", "#4b5563"), "Term": ("#eef2ff", "#3730a3"),
}
ALERT = {  # kind: (background, text, stripe, icon)
    "success": ("#ecfdf5", "#065f46", "#10b981", "\u2714"), "warning": ("#fffbeb", "#92400e", "#f59e0b", "!"),
    "error": ("#fef2f2", "#991b1b", "#ef4444", "\u2716"), "info": ("#eff6ff", "#1e40af", "#3b82f6", "i"),
}
COURSE_COLORS = [("#dbeafe", "#3b82f6"), ("#dcfce7", "#22c55e"), ("#fef3c7", "#f59e0b"), ("#fce7f3", "#ec4899"),
                 ("#ede9fe", "#8b5cf6"), ("#ccfbf1", "#14b8a6"), ("#ffedd5", "#f97316"), ("#e5e7eb", "#6b7280")]
NAV = {
    "student": [("MAIN", [("dash", "Dashboard")]),
                ("REGISTRATION", [("catalog", "Course Registration"), ("myreg", "My Registration"),
                                  ("sched", "Weekly Schedule"), ("exams", "Exam Schedule")]),
                ("ACADEMIC", [("trans", "Transcript")]),
                ("ACCOUNT", [("notif", "Notifications"), ("acct", "Account")])],
    "advisor": [("ADVISING", [("approvals", "Approvals"), ("advisees", "My Advisees"), ("autocap", "Capacity Automation")]),
                ("ACCOUNT", [("notif", "Notifications"), ("acct", "Account")])],
    "admin": [("REGISTRAR", [("offerings", "Offerings"), ("control", "Registration Control"), ("ann", "Announcements")]),
              ("ACCOUNT", [("acct", "Account")])],
}
PAGE_SUB = {
    "dash": "Your term at a glance", "catalog": "Browse this term's offerings and add courses",
    "myreg": "Submit your selection to your advisor for approval", "sched": "Your weekly timetable",
    "exams": "Exam dates for your registered courses", "trans": "Your grades, GPA and earned AKTS",
    "notif": "Updates about your registration", "acct": "Profile and password",
    "approvals": "Review the courses your advisees submitted", "advisees": "Academic standing of your students",
    "autocap": "Add seats automatically when a waitlist builds up",
    "offerings": "Seats, waitlists and rosters", "control": "Registration window and term control",
    "ann": "Messages shown on every student's dashboard",
}


def nav_keys(role):
    return [k for _, items in NAV[role] for k, _ in items]


def reg_steps(regs):
    """Progress tracker states for the select -> submit -> approve flow."""
    titles = ("Select courses", "Submit to advisor", "Advisor approval")
    if not regs:
        states = ("active", "todo", "todo")
    elif any(r["status"] == "Draft" for r in regs):
        states = ("done", "active", "todo")
    elif any(r["status"] == "Pending" for r in regs):
        states = ("done", "done", "active")
    else:
        states = ("done", "done", "done")
    return list(zip(titles, states))


def clear(widget):
    for w in widget.winfo_children():
        w.destroy()


def fill_transcript(tree, conn, sid):
    tree.delete(*tree.get_children())
    rows = transcript_rows(conn, sid)
    for term in sorted({r["term"] for r in rows}):
        trs = [r for r in rows if r["term"] == term]
        parent = tree.insert("", "end", text=f"{term}     term GPA {calc_gpa(trs):.2f}", open=True)
        for r in trs:
            tree.insert(parent, "end", values=(r["code"], r["name"], r["credits"], r["grade"], f"{GRADE_POINTS[r['grade']]:.1f}"),
                        tags=("fail" if r["grade"] in FAIL_GRADES else "ok",))
    g = cgpa(rows)
    return "No completed courses yet." if g is None else f"CGPA {g:.2f}     Earned AKTS {earned_credits(rows)}"


class ScrollFrame(tk.Frame):
    """Vertically scrollable page. Children go into .body, which stretches to fill the window
    and only scrolls (with an auto-hiding scrollbar) when the content is taller than the window."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, yscrollincrement=px(24))
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._set_bar)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self._bar_on, self._need = False, 0
        self.body.bind("<Configure>", self._layout)
        self.canvas.bind("<Configure>", self._layout)

    def _layout(self, _e=None):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        self._need = self.body.winfo_reqheight()
        total = max(h, self._need)
        self.canvas.itemconfigure(self._win, width=w, height=total)
        self.canvas.configure(scrollregion=(0, 0, w, total))

    def _set_bar(self, lo, hi):
        self.bar.set(lo, hi)
        need_bar = float(lo) > 0.0 or float(hi) < 1.0
        if need_bar and not self._bar_on:
            self.bar.pack(side="right", fill="y", before=self.canvas)
            self._bar_on = True
        elif not need_bar and self._bar_on:
            self.bar.pack_forget()
            self._bar_on = False

    def can_scroll(self):
        return self._need > self.canvas.winfo_height()

    def scroll(self, units):
        if self.can_scroll():
            self.canvas.yview_scroll(units, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)


class Dialog(tk.Toplevel):
    """Modern modal dialog: colour strip, icon badge, title, message, optional input fields, action buttons."""

    def __init__(self, app, title, message, kind="info", confirm="OK", cancel="Cancel", danger=False,
                 fields=None, validator=None, parent=None):
        super().__init__(parent or app)
        self.withdraw()
        self.validator, self.result = validator, None
        self.title(title)
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(parent or app)
        bg, fg, stripe, icon = ALERT.get(kind, ALERT["info"])
        tk.Frame(self, bg=stripe, height=px(5)).pack(fill="x")
        body = tk.Frame(self, bg=CARD, padx=px(26), pady=px(22))
        body.pack(fill="both", expand=True)
        head = tk.Frame(body, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=icon, bg=bg, fg=stripe, font=app.f["h2"], width=3, pady=px(6)).pack(side="left")
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=app.f["h2"], padx=px(12)).pack(side="left")
        tk.Label(body, text=message, bg=CARD, fg=MUTED, justify="left", anchor="w", wraplength=px(400)).pack(fill="x", pady=(px(14), 0))
        self.vars, first = [], None
        for label, initial in (fields or []):
            tk.Label(body, text=label.upper(), bg=CARD, fg=MUTED, font=app.f["sbold"], anchor="w").pack(fill="x", pady=(px(14), px(3)))
            var = tk.StringVar(value=initial)
            ent = ttk.Entry(body, textvariable=var, width=46)
            ent.pack(fill="x")
            self.vars.append(var)
            first = first or ent
        self.err = tk.Label(body, text="", bg=CARD, fg="#b91c1c", anchor="w")
        self.err.pack(fill="x", pady=(px(6), 0))
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(px(14), 0))
        ttk.Button(row, text=confirm, style="Danger.TButton" if danger else "Accent.TButton", command=self.ok).pack(side="right")
        if cancel:
            ttk.Button(row, text=cancel, command=self.cancel).pack(side="right", padx=(0, px(10)))
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        app.center(self, over=parent or app)
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.deiconify()
        app.fade_in(self, 1)
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        (first or self).focus_set()
        if first:
            first.select_range(0, "end")

    def ok(self):
        values = [v.get() for v in self.vars]
        if self.validator:
            err = self.validator(values)
            if err:
                self.err.config(text=err)
                return
        self.result = values if self.vars else True
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        global UI_SCALE
        try:
            UI_SCALE = max(1.0, min(2.5, float(self.winfo_fpixels("1i")) / 96.0))
        except (tk.TclError, TypeError, ValueError):
            UI_SCALE = 1.0
        self.title("Course Registration System")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(1280), sw - px(60)), min(px(780), sh - px(100))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - px(20))}")
        self.minsize(min(px(1100), w), min(px(680), h))
        self.tree_state = {}
        self.catalog_search = None
        self.bind("<Control-f>", self.focus_search)
        # Wheel scrolling follows the mouse pointer on every platform: tables scroll themselves, the page scrolls otherwise.
        for cls in ("Treeview", "TCombobox"):
            self.unbind_class(cls, "<MouseWheel>")
            if sys.platform.startswith("linux"):
                self.unbind_class(cls, "<Button-4>")
                self.unbind_class(cls, "<Button-5>")
        self.bind_all("<MouseWheel>", self.on_wheel)
        if sys.platform.startswith("linux"):
            self.bind_all("<Button-4>", self.on_wheel)
            self.bind_all("<Button-5>", self.on_wheel)
        self.conn = connect()
        self.user = None
        self.pages, self.nav, self.current = {}, {}, None
        self.shown, self._poll_id, self._status_key = set(), None, None
        self.logo = None
        self.setup_style()
        self.root_frame = tk.Frame(self, bg=BG)
        self.root_frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show_login()

    # ---------------- style & small widgets ----------------
    def setup_style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        fams = set(tkfont.families(self))
        fam = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Roboto", "Ubuntu", "DejaVu Sans")
                    if f in fams), tkfont.nametofont("TkDefaultFont").actual("family"))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=fam, size=10)
        mk = lambda **k: tkfont.Font(family=fam, **k)
        self.f = dict(base=mk(size=10), small=mk(size=9), bold=mk(size=10, weight="bold"), sbold=mk(size=9, weight="bold"),
                      h1=mk(size=19, weight="bold"), h2=mk(size=12, weight="bold"), stat=mk(size=22, weight="bold"),
                      brand=mk(size=15, weight="bold"), hero=mk(size=28, weight="bold"))
        s.configure(".", background=BG, foreground=TEXT, font=self.f["base"])
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
        s.configure("TEntry", padding=(px(8), px(7)), fieldbackground="white", bordercolor="#d1d5db", lightcolor="#d1d5db", darkcolor="#d1d5db")
        s.configure("TCombobox", padding=(px(6), px(6)), fieldbackground="white", bordercolor="#d1d5db", arrowsize=px(14))
        s.configure("TButton", padding=(px(14), px(7)), relief="flat", background="white", foreground=TEXT, bordercolor="#d1d5db",
                    borderwidth=1, focusthickness=0, focuscolor="white")
        s.map("TButton", background=[("active", "#f3f4f6"), ("disabled", "#f9fafb")], foreground=[("disabled", "#9ca3af")])
        s.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT, padding=(px(16), px(8)),
                    focusthickness=0, focuscolor=ACCENT)
        s.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#93c5fd")],
              foreground=[("disabled", "white")], bordercolor=[("disabled", "#93c5fd")])
        s.configure("Danger.TButton", background="#dc2626", foreground="white", bordercolor="#dc2626", padding=(px(14), px(7)),
                    focusthickness=0, focuscolor="#dc2626")
        s.map("Danger.TButton", background=[("active", "#b91c1c"), ("disabled", "#fca5a5")], foreground=[("disabled", "white")])
        try:  # borderless table
            s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except tk.TclError:
            pass
        s.configure("Treeview", rowheight=px(36), background="white", fieldbackground="white", foreground=TEXT, borderwidth=0)
        s.configure("Treeview.Heading", background="#f9fafb", foreground=MUTED, font=self.f["sbold"], relief="flat",
                    padding=(px(8), px(9)), borderwidth=0)
        s.map("Treeview.Heading", background=[("active", "#f3f4f6")])
        s.configure("Vertical.TScrollbar", background="#d1d5db", troughcolor="#f9fafb", borderwidth=0, arrowsize=px(12))

        def fixed_map(option):  # workaround: row tag colours not showing on some Tk versions
            return [e for e in s.map("Treeview", query_opt=option) if e[:2] != ("!disabled", "!selected")]
        s.map("Treeview", foreground=[("selected", "#111827")] + fixed_map("foreground"),
              background=[("selected", "#dbeafe")] + fixed_map("background"))

    def card(self, parent, accent=None):
        outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        if accent:
            tk.Frame(outer, bg=accent, height=px(3)).pack(fill="x")
        inner = tk.Frame(outer, bg=CARD, padx=px(18), pady=px(14))
        inner.pack(fill="both", expand=True)
        outer.body = inner
        return outer

    def chip(self, parent, text, bg, fg):
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=self.f["sbold"], padx=px(10), pady=px(3))

    def meter(self, parent, value, limit, width=140, bg=CARD):
        w, h = px(width), px(8)
        cv = tk.Canvas(parent, width=w, height=h, bg=bg, highlightthickness=0)
        cv.create_rectangle(0, 0, w, h, fill="#e5e7eb", outline="")
        frac = 0 if not limit else min(1.0, value / limit)
        if frac:
            cv.create_rectangle(0, 0, int(w * frac), h, fill="#f59e0b" if frac >= 0.9 else ACCENT, outline="")
        return cv

    def avatar(self, parent, name, size=36, bg=CARD):
        initials = "".join(p[0] for p in name.replace(".", " ").split()[:2]).upper() or "?"
        sz = px(size)
        cv = tk.Canvas(parent, width=sz, height=sz, bg=bg, highlightthickness=0)
        cv.create_oval(1, 1, sz - 1, sz - 1, fill=ACCENT, outline="")
        cv.create_text(sz / 2, sz / 2, text=initials, fill="white", font=self.f["sbold"] if size < 50 else self.f["h2"])
        return cv

    def stat_card(self, parent, label, value, sub="", accent=ACCENT):
        c = self.card(parent, accent)
        tk.Label(c.body, text=label.upper(), bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(anchor="w")
        tk.Label(c.body, text=value, bg=CARD, fg=TEXT, font=self.f["stat"]).pack(anchor="w", pady=(px(2), px(0)))
        tk.Label(c.body, text=sub or " ", bg=CARD, fg=MUTED, font=self.f["small"]).pack(anchor="w")
        return c

    def stat_row(self, parent, specs):
        row = tk.Frame(parent, bg=BG)
        for i, spec in enumerate(specs):
            self.stat_card(row, *spec).grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(14), 0))
            row.columnconfigure(i, weight=1, uniform="stat")
        return row

    def stepper(self, parent, steps, bg=CARD):
        row = tk.Frame(parent, bg=bg)
        look = {"done": ("#10b981", "white"), "active": (ACCENT, "white"), "todo": ("#e5e7eb", MUTED)}
        for i, (title, state) in enumerate(steps):
            bgc, fgc = look[state]
            tk.Label(row, text="\u2714" if state == "done" else str(i + 1), bg=bgc, fg=fgc, width=3, font=self.f["sbold"],
                     pady=px(3)).pack(side="left")
            tk.Label(row, text=title, bg=bg, fg=TEXT if state != "todo" else MUTED, padx=px(8),
                     font=self.f["bold"] if state == "active" else self.f["base"]).pack(side="left")
            if i < len(steps) - 1:
                tk.Frame(row, bg=BORDER, height=px(2), width=px(36)).pack(side="left", padx=px(8))
        return row

    def mk_tree(self, parent, cols, tree_mode=False):
        holder = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        tree = ttk.Treeview(holder, columns=[c[0] for c in cols], show="tree headings" if tree_mode else "headings",
                            selectmode="browse")
        heads = {}
        for key, head, width in cols:
            heads[key] = head
            kw = {} if tree_mode else {"command": (lambda k=key: self.sort_tree(tree, k))}
            tree.heading(key, text=head, anchor="w", **kw)
            tree.column(key, width=px(width), anchor="w", stretch=True)
        self.tree_state[tree] = {"heads": heads, "sort": None, "empty": None}
        sb = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("odd", background="#fafbfc")
        tree.tag_configure("even", background="#ffffff")
        return holder, tree

    def add_row(self, tree, values, iid=None, tags=(), parent=""):
        tags = tuple(tags) or (("odd",) if len(tree.get_children(parent)) % 2 else ("even",))
        opts = dict(values=values, tags=tags)
        if iid is not None:
            opts["iid"] = iid
        return tree.insert(parent, "end", **opts)

    def status_tags(self, tree):
        for name in ("Approved", "Pending", "Draft", "Waitlisted"):
            tree.tag_configure(name, background=CHIP[name][0])
        tree.tag_configure("Full", foreground="#b91c1c")
        tree.tag_configure("Passed", foreground="#9ca3af")
        tree.tag_configure("Closed", foreground="#9ca3af")

    def on_close(self):
        if self._poll_id:
            self.after_cancel(self._poll_id)
        self.conn.close()
        self.destroy()


    # ---------------- table helpers ----------------
    def sort_tree(self, tree, key):
        st = self.tree_state.get(tree)
        if st is None:
            return
        prev = st["sort"]
        reverse = (not prev[1]) if prev and prev[0] == key else False
        st["sort"] = (key, reverse)

        def sort_key(iid):
            v = str(tree.set(iid, key)).replace("\u25cf", "").strip()
            head = v.split("/")[0].replace("#", "").strip()
            try:
                return (0, float(head), "")
            except ValueError:
                return (1, 0.0, v.lower())
        for idx, iid in enumerate(sorted(tree.get_children(""), key=sort_key, reverse=reverse)):
            tree.move(iid, "", idx)
            tags = tree.item(iid, "tags")
            if not tags or set(tags) <= {"odd", "even"}:
                tree.item(iid, tags=("odd" if idx % 2 else "even",))
        for k, h in st["heads"].items():
            tree.heading(k, text=h + (("  \u25bc" if reverse else "  \u25b2") if k == key else ""))

    def empty_state(self, tree, text):
        st = self.tree_state.setdefault(tree, {"heads": {}, "sort": None, "empty": None})
        if st["empty"] is None:
            st["empty"] = tk.Label(tree.master, text=text, bg=CARD, fg=MUTED, font=self.f["h2"])
        lbl = st["empty"]
        lbl.config(text=text)
        if tree.get_children():
            lbl.place_forget()
        else:
            lbl.place(relx=0.5, rely=0.45, anchor="center")

    def focus_search(self, _e=None):
        if self.user and self.user["role"] == "student" and self.catalog_search is not None:
            self.show_page("catalog")
            try:
                self.catalog_search.focus_set()
            except tk.TclError:
                pass

    # ---------------- pop-up windows & dialogs ----------------
    def center(self, win, w=None, h=None, over=None):
        over = over or self
        win.update_idletasks()
        w, h = w or win.winfo_reqwidth(), h or win.winfo_reqheight()
        x = over.winfo_rootx() + (over.winfo_width() - w) // 2
        y = over.winfo_rooty() + (over.winfo_height() - h) // 3
        win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

    def fade_in(self, win, step=0):
        try:
            a = min(1.0, step / 6)
            win.attributes("-alpha", a)
            if a < 1.0:
                win.after(18, lambda: self.fade_in(win, step + 1))
        except tk.TclError:
            pass

    def popup(self, title, subtitle="", w=720, h=520, parent=None):
        """A modern secondary window: white header, grey body, footer with a Close button."""
        win = tk.Toplevel(parent or self)
        win.withdraw()
        win.title(title)
        win.configure(bg=BG)
        win.transient(parent or self)
        win.minsize(px(480), px(360))
        head = tk.Frame(win, bg=CARD, padx=px(22), pady=px(14))
        head.pack(fill="x")
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=self.f["h1"]).pack(anchor="w")
        sub = tk.Label(head, text=subtitle, bg=CARD, fg=MUTED)
        sub.pack(anchor="w")
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")
        foot = tk.Frame(win, bg=BG, padx=px(22), pady=px(14))
        foot.pack(side="bottom", fill="x")
        body = tk.Frame(win, bg=BG, padx=px(22), pady=px(16))
        body.pack(fill="both", expand=True)
        ttk.Button(foot, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        self.center(win, px(w), px(h), parent or self)
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        win.deiconify()
        self.fade_in(win, 1)
        return win, body, foot, sub

    def confirm(self, title, message, confirm="Confirm", danger=False, kind="warning", parent=None):
        d = Dialog(self, title, message, kind=kind, confirm=confirm, danger=danger, parent=parent)
        self.wait_window(d)
        return d.result is True

    def ask_fields(self, title, message, fields, confirm="OK", validator=None, danger=False, parent=None):
        d = Dialog(self, title, message, kind="info", confirm=confirm, danger=danger, fields=fields,
                   validator=validator, parent=parent)
        self.wait_window(d)
        return d.result

    def ask_text(self, title, message, label="Text", confirm="OK", danger=False, initial=""):
        r = self.ask_fields(title, message, [(label, initial)], confirm, danger=danger)
        return r[0] if r else None

    def ask_int(self, title, message, initial, lo, hi):
        def check(v):
            try:
                n = int(v[0])
            except ValueError:
                return "Enter a whole number."
            return None if lo <= n <= hi else f"Enter a number between {lo} and {hi}."
        r = self.ask_fields(title, message, [("Value", str(initial))], "Save", check)
        return int(r[0]) if r else None

    def wheel_units(self, e):
        num = getattr(e, "num", 0)
        if num == 4:
            return -3
        if num == 5:
            return 3
        d = getattr(e, "delta", 0) or 0
        if not d:
            return 0
        if sys.platform == "darwin":
            return -int(d) or (-1 if d > 0 else 1)
        return -int(d / 120) * 3 or (-1 if d > 0 else 1)

    def on_wheel(self, e):
        try:
            if self.user is None:
                return
            units = self.wheel_units(e)
            node = self.winfo_containing(e.x_root, e.y_root)
            table, inside = None, False
            while node is not None:
                cls = node.winfo_class()
                if cls in ("Listbox", "Text"):
                    return
                if cls == "Treeview" and table is None:
                    table = node
                if node is self.content:
                    inside = True
                    break
                node = node.master
            if not units:
                return
            if table is not None:
                lo, hi = table.yview()
                at_end = (units > 0 and hi >= 1.0) or (units < 0 and lo <= 0.0)
                if not at_end or not inside:
                    table.yview_scroll(units, "units")
                    return
            if inside and self.current:
                self.pages[self.current][0].scroll(units)
        except (tk.TclError, KeyError, AttributeError):
            pass

    # ---------------- alerts ----------------
    def banner(self, text, kind="info", nid=None, auto=0):
        bg, fg, stripe, icon = ALERT.get(kind, ALERT["info"])
        kids = self.banner_box.winfo_children()
        if len(kids) >= 4:
            kids[0].destroy()
        row = tk.Frame(self.banner_box, bg=bg, highlightthickness=1, highlightbackground=stripe)
        row.pack(fill="x", pady=(px(12), px(0)))
        tk.Frame(row, bg=stripe, width=px(5)).pack(side="left", fill="y")
        tk.Label(row, text=icon, bg=bg, fg=stripe, font=self.f["bold"], padx=px(12)).pack(side="left")
        tk.Label(row, text=text, bg=bg, fg=fg, anchor="w", justify="left", wraplength=px(800), pady=px(10)).pack(side="left", fill="x", expand=True)
        close = tk.Label(row, text="\u2715", bg=bg, fg=fg, cursor="hand2", padx=px(14))
        close.pack(side="right")

        def dismiss(_e=None):
            if nid:
                mark_seen(self.conn, nid)
            row.destroy()
            self.update_badge()
        close.bind("<Button-1>", dismiss)
        if nid:
            self.shown.add(nid)
        if auto:
            self.after(auto, lambda: row.winfo_exists() and row.destroy())

    def flash(self, text, kind="info"):
        self.banner(text, kind, auto=7000)

    def poll(self):
        self._poll_id = None
        try:
            new = [n for n in unseen_notifications(self.conn, self.user["id"]) if n["id"] not in self.shown]
            for n in new:
                self.banner(n["message"], n["kind"], n["id"])
            self.update_badge()
            self.update_status()
            if new and self.current:
                self.pages[self.current][1]()
        except sqlite3.Error:
            pass
        self._poll_id = self.after(4000, self.poll)

    def update_badge(self):
        if "notif" in self.nav:
            n = len(unseen_notifications(self.conn, self.user["id"]))
            self.nav["notif"][2].config(text="Notifications" + (f"   \u25cf {n}" if n else ""))

    def update_status(self):
        cur, is_open = get_setting(self.conn, "current_term"), reg_is_open(self.conn)
        used = limit = None
        if self.user["role"] == "student":
            used = reg_credits(self.conn, self.user["id"])
            limit = credit_limit_from_rows(transcript_rows(self.conn, self.user["id"]))
        key = (cur, is_open, used, limit)
        if key == self._status_key:
            return
        self._status_key = key
        clear(self.chips)
        if used is not None:
            tk.Label(self.chips, text=f"AKTS  {used} / {limit}", bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(side="left", padx=(px(0), px(8)))
            self.meter(self.chips, used, limit, 120).pack(side="left", padx=(px(0), px(18)))
        self.chip(self.chips, cur, *CHIP["Term"]).pack(side="left", padx=px(4))
        self.chip(self.chips, "Registration OPEN" if is_open else "Registration CLOSED",
                  *(CHIP["Approved"] if is_open else CHIP["Full"])).pack(side="left", padx=(px(4), px(0)))

    # ---------------- login ----------------
    def show_login(self):
        if self._poll_id:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self.user, self.pages, self.nav, self.current, self._status_key = None, {}, {}, None, None
        self.catalog_search = None
        self.shown = set()
        clear(self.root_frame)
        wrap = tk.Frame(self.root_frame, bg=CARD)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=5, uniform="lg")
        wrap.columnconfigure(1, weight=6, uniform="lg")
        wrap.rowconfigure(0, weight=1)
        left = tk.Frame(wrap, bg=SIDEBAR)
        left.grid(row=0, column=0, sticky="nsew")
        right = tk.Frame(wrap, bg=CARD)
        right.grid(row=0, column=1, sticky="nsew")

        hero = tk.Frame(left, bg=SIDEBAR)
        hero.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(hero, text="Course\nRegistration", bg=SIDEBAR, fg="white", font=self.f["hero"], justify="left").pack(anchor="w")
        tk.Label(hero, text="Student Information System", bg=SIDEBAR, fg="#8da2c0", font=self.f["h2"]).pack(anchor="w", pady=(px(6), px(28)))
        for line in ("Pick courses and see conflicts instantly", "Advisor approval, step by step",
                     "Automatic waitlists with live alerts", "Transcript, GPA and AKTS load in one place"):
            tk.Label(hero, text=f"\u2714   {line}", bg=SIDEBAR, fg="#c7d2e4", anchor="w").pack(anchor="w", pady=px(4))

        box = tk.Frame(right, bg=CARD)
        box.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(box, text="Sign in", bg=CARD, fg=TEXT, font=self.f["h1"]).pack(anchor="w")
        tk.Label(box, text="Use your student or staff number", bg=CARD, fg=MUTED).pack(anchor="w", pady=(px(2), px(20)))
        self.id_var, self.pw_var = tk.StringVar(), tk.StringVar()
        tk.Label(box, text="Number", bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(anchor="w")
        e1 = ttk.Entry(box, textvariable=self.id_var, width=38)
        e1.pack(pady=(px(4), px(12)), fill="x")
        tk.Label(box, text="Password", bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(anchor="w")
        e2 = ttk.Entry(box, textvariable=self.pw_var, show="*", width=38)
        e2.pack(pady=(px(4), px(6)), fill="x")
        self.login_err = tk.StringVar()
        tk.Label(box, textvariable=self.login_err, bg=CARD, fg="#b91c1c", anchor="w").pack(fill="x", pady=(px(0), px(10)))
        for v in (self.id_var, self.pw_var):
            v.trace_add("write", lambda *a: self.login_err.set(""))
        ttk.Button(box, text="Sign in", style="Accent.TButton", command=self.do_login).pack(fill="x")
        hint = tk.Frame(box, bg="#f3f4f6", padx=px(14), pady=px(10))
        hint.pack(fill="x", pady=(px(22), px(0)))
        tk.Label(hint, text="Demo accounts", bg="#f3f4f6", fg=TEXT, font=self.f["sbold"]).pack(anchor="w")
        for line in ("Student   20260001 - 20260008   /   1234", "Advisor   A1001, A1002   /   1234", "Admin      admin   /   admin"):
            tk.Label(hint, text=line, bg="#f3f4f6", fg=MUTED, font=self.f["small"]).pack(anchor="w", pady=px(1))
        for e in (e1, e2):
            e.bind("<Return>", lambda ev: self.do_login())
        e1.focus()

    def do_login(self):
        u = authenticate(self.conn, self.id_var.get(), self.pw_var.get())
        if u is None:
            self.login_err.set("Wrong number or password. Please try again.")
            return
        self.user = u
        self.show_main()

    # ---------------- main window ----------------
    def show_main(self):
        clear(self.root_frame)
        role = self.user["role"]
        body = tk.Frame(self.root_frame, bg=BG)
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=SIDEBAR, width=px(250))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        # user card (packed first so it always keeps its place at the bottom)
        ucard = tk.Frame(side, bg="#0b1524", padx=px(16), pady=px(14))
        ucard.pack(side="bottom", fill="x")
        self.avatar(ucard, self.user["name"], 38, "#0b1524").pack(side="left")
        info = tk.Frame(ucard, bg="#0b1524")
        info.pack(side="left", padx=px(10))
        tk.Label(info, text=self.user["name"], bg="#0b1524", fg="white", font=self.f["bold"], anchor="w").pack(anchor="w")
        sub = self.user["id"] if role == "student" else role.title()
        tk.Label(info, text=sub, bg="#0b1524", fg="#8da2c0", font=self.f["small"], anchor="w").pack(anchor="w")
        out = tk.Label(ucard, text="Sign out", bg="#0b1524", fg="#8da2c0", cursor="hand2", font=self.f["small"])
        out.pack(side="right")
        out.bind("<Button-1>", lambda e: self.show_login())

        brand = tk.Frame(side, bg=SIDEBAR, padx=px(22), pady=px(22))
        brand.pack(fill="x")
        if os.path.exists(LOGO_PATH):
            try:
                self.logo = tk.PhotoImage(file=LOGO_PATH)
                tk.Label(brand, image=self.logo, bg=SIDEBAR).pack(anchor="w", pady=(px(0), px(8)))
            except tk.TclError:
                pass
        tk.Label(brand, text="Course Registration", bg=SIDEBAR, fg="white", font=self.f["brand"]).pack(anchor="w")
        tk.Label(brand, text="Student Information System", bg=SIDEBAR, fg="#8da2c0", font=self.f["small"]).pack(anchor="w")

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        header = tk.Frame(right, bg=CARD, padx=px(26), pady=px(16))
        header.pack(fill="x")
        titles = tk.Frame(header, bg=CARD)
        titles.pack(side="left")
        self.title_var, self.sub_var = tk.StringVar(), tk.StringVar()
        tk.Label(titles, textvariable=self.title_var, bg=CARD, fg=TEXT, font=self.f["h1"]).pack(anchor="w")
        tk.Label(titles, textvariable=self.sub_var, bg=CARD, fg=MUTED).pack(anchor="w")
        self.chips = tk.Frame(header, bg=CARD)
        self.chips.pack(side="right")
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x")
        self.banner_box = tk.Frame(right, bg=BG, padx=px(26))
        self.banner_box.pack(fill="x")
        self.content = tk.Frame(right, bg=BG, padx=px(26), pady=px(20))
        self.content.pack(fill="both", expand=True)

        builders = {"dash": self.page_dash, "catalog": self.page_catalog, "myreg": self.page_myreg,
                    "sched": self.page_sched, "exams": self.page_exams, "trans": self.page_trans,
                    "notif": self.page_notif, "acct": self.page_acct, "approvals": self.page_approvals,
                    "advisees": self.page_advisees, "offerings": self.page_offerings,
                    "control": self.page_control, "ann": self.page_ann, "autocap": self.page_autocap}
        self.titles = {}
        nav_box = tk.Frame(side, bg=SIDEBAR)
        nav_box.pack(fill="x")
        first = None
        for section, items in NAV[role]:
            tk.Label(nav_box, text=section, bg=SIDEBAR, fg="#6b7f9e", font=self.f["sbold"], anchor="w",
                     padx=px(24)).pack(fill="x", pady=(px(14), px(4)))
            for key, label in items:
                row = tk.Frame(nav_box, bg=SIDEBAR, cursor="hand2")
                row.pack(fill="x")
                ind = tk.Frame(row, bg=SIDEBAR, width=px(4))
                ind.pack(side="left", fill="y")
                lbl = tk.Label(row, text=label, bg=SIDEBAR, fg="#d6deeb", anchor="w", padx=px(20), pady=px(10))
                lbl.pack(side="left", fill="x", expand=True)
                self.nav[key] = (row, ind, lbl)
                self.titles[key] = label
                for w in (row, lbl):
                    w.bind("<Button-1>", lambda e, k=key: self.show_page(k))
                    w.bind("<Enter>", lambda e, k=key: self.hover(k, True))
                    w.bind("<Leave>", lambda e, k=key: self.hover(k, False))
                frame = ScrollFrame(self.content)
                self.pages[key] = (frame, builders[key](frame.body))
                first = first or key
        self.update_status()
        self.show_page(first)
        self.poll()

    def paint_nav(self, key, state):
        row, ind, lbl = self.nav[key]
        bg = {"active": SIDEBAR_ACTIVE, "hover": SIDEBAR_HOVER, "idle": SIDEBAR}[state]
        row.config(bg=bg)
        lbl.config(bg=bg, fg="white" if state == "active" else "#d6deeb", font=self.f["bold"] if state == "active" else self.f["base"])
        ind.config(bg=ACCENT if state == "active" else bg)

    def hover(self, key, inside):
        if key != self.current:
            self.paint_nav(key, "hover" if inside else "idle")

    def show_page(self, key):
        if self.current:
            self.pages[self.current][0].pack_forget()
        self.current = key
        for k in self.nav:
            self.paint_nav(k, "active" if k == key else "idle")
        self.title_var.set(self.titles[key])
        self.sub_var.set(PAGE_SUB.get(key, ""))
        frame, refresh = self.pages[key]
        frame.pack(fill="both", expand=True)
        frame.to_top()
        refresh()
        self.update_status()

    # ======================= STUDENT PAGES =======================
    def page_dash(self, parent):
        def refresh():
            clear(parent)
            sid, cur = self.user["id"], get_setting(self.conn, "current_term")
            rows = transcript_rows(self.conn, sid)
            regs, wl = my_regs(self.conn, sid), my_waitlist(self.conn, sid)
            g = cgpa(rows)
            is_open = reg_is_open(self.conn)
            self.stat_row(parent, [
                ("Current term", cur, "Registration is open" if is_open else "Registration is closed", ACCENT),
                ("AKTS this term", f"{reg_credits(self.conn, sid)} / {credit_limit_from_rows(rows)}", "course load", "#8b5cf6"),
                ("CGPA", "-" if g is None else f"{g:.2f}", f"{len(latest_attempts(rows))} graded courses", "#10b981"),
                ("Earned AKTS", str(earned_credits(rows)), "passed so far", "#f59e0b")]).pack(fill="x")
            cols = tk.Frame(parent, bg=BG)
            cols.pack(fill="both", expand=True, pady=(px(14), px(0)))
            cols.columnconfigure(0, weight=3, uniform="d")
            cols.columnconfigure(1, weight=2, uniform="d")
            cols.rowconfigure(0, weight=1)
            left = tk.Frame(cols, bg=BG)
            left.grid(row=0, column=0, sticky="nsew", padx=(px(0), px(14)))

            counts = {s: sum(1 for r in regs if r["status"] == s) for s in ("Draft", "Pending", "Approved")}
            if not is_open:
                hint = "Registration is closed. Contact the registrar if you need a change."
            elif not regs:
                hint = "You have not picked any courses yet. Open Course Registration to add some."
            elif counts["Draft"]:
                hint = f"You have {counts['Draft']} draft course(s). Submit them for advisor approval in My Registration."
            elif counts["Pending"]:
                hint = f"{counts['Pending']} course(s) are waiting for your advisor's approval."
            else:
                hint = "All your courses are approved. You are done for this term!"
            if wl:
                hint += f"  You are on {len(wl)} waitlist(s)."
            prog = self.card(left)
            prog.pack(fill="x")
            tk.Label(prog.body, text="Registration progress", bg=CARD, font=self.f["h2"]).pack(anchor="w")
            self.stepper(prog.body, reg_steps(regs)).pack(anchor="w", pady=(px(14), px(10)))
            tk.Label(prog.body, text=hint, bg=CARD, fg=MUTED, justify="left", wraplength=px(560)).pack(anchor="w")

            crs = self.card(left)
            crs.pack(fill="both", expand=True, pady=(px(14), px(0)))
            tk.Label(crs.body, text="My courses this term", bg=CARD, font=self.f["h2"]).pack(anchor="w", pady=(px(0), px(6)))
            if not regs and not wl:
                tk.Label(crs.body, text="Nothing yet.", bg=CARD, fg=MUTED).pack(anchor="w")
            for r in regs:
                line = tk.Frame(crs.body, bg=CARD)
                line.pack(fill="x", pady=px(4))
                tk.Label(line, text=r["code"], bg=CARD, font=self.f["bold"], width=8, anchor="w").pack(side="left")
                tk.Label(line, text=r["name"], bg=CARD, anchor="w").pack(side="left")
                self.chip(line, r["status"], *CHIP[r["status"]]).pack(side="right")
                tk.Label(line, text=f"{r['credits']} AKTS", bg=CARD, fg=MUTED).pack(side="right", padx=px(12))
            for r, pos in wl:
                line = tk.Frame(crs.body, bg=CARD)
                line.pack(fill="x", pady=px(4))
                tk.Label(line, text=r["code"], bg=CARD, font=self.f["bold"], width=8, anchor="w").pack(side="left")
                tk.Label(line, text=r["name"], bg=CARD, anchor="w").pack(side="left")
                self.chip(line, f"Waitlist #{pos}", *CHIP["Waitlisted"]).pack(side="right")

            ann = self.card(cols)
            ann.grid(row=0, column=1, sticky="nsew")
            tk.Label(ann.body, text="Announcements", bg=CARD, font=self.f["h2"]).pack(anchor="w", pady=(px(0), px(4)))
            for a in get_announcements(self.conn, 4):
                tk.Label(ann.body, text=a["title"], bg=CARD, fg=TEXT, font=self.f["bold"]).pack(anchor="w", pady=(px(10), px(0)))
                tk.Label(ann.body, text=a["created"], bg=CARD, fg=MUTED, font=self.f["small"]).pack(anchor="w")
                tk.Label(ann.body, text=a["body"], bg=CARD, fg=MUTED, justify="left", wraplength=px(360)).pack(anchor="w", pady=(px(2), px(0)))
        return refresh

    def page_catalog(self, parent):
        cur = get_setting(self.conn, "current_term")
        term_var, level_var = tk.StringVar(value=cur), tk.StringVar(value="All")
        search_var, hide_var = tk.StringVar(), tk.BooleanVar(value=False)
        bar = self.card(parent)
        bar.pack(fill="x", pady=(px(0), px(12)))
        top = bar.body
        top.config(pady=px(10))

        def lab(text):
            tk.Label(top, text=text, bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(side="left", padx=(px(0), px(6)))
        lab("TERM")
        ttk.Combobox(top, textvariable=term_var, values=terms_with_offerings(self.conn), state="readonly", width=14).pack(side="left", padx=(px(0), px(16)))
        lab("SEMESTER")
        ttk.Combobox(top, textvariable=level_var, values=["All"] + [f"Semester {i}" for i in range(1, 9)],
                     state="readonly", width=12).pack(side="left", padx=(px(0), px(16)))
        lab("SEARCH")
        self.catalog_search = ttk.Entry(top, textvariable=search_var, width=24)
        self.catalog_search.pack(side="left", padx=(px(0), px(16)))
        ttk.Checkbutton(top, text="Hide passed courses", variable=hide_var, style="Card.TCheckbutton").pack(side="left")

        cols = [("code", "CODE", 70), ("name", "COURSE", 210), ("cr", "AKTS", 50), ("sem", "SEM.", 50), ("inst", "INSTRUCTOR", 95),
                ("sched", "SCHEDULE", 190), ("seats", "SEATS", 65), ("pre", "PREREQ", 70), ("status", "STATUS", 110)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        self.status_tags(tree)

        det = self.card(parent, ACCENT)
        det.pack(fill="x", pady=(px(12), px(0)))
        ttl_var, meta_var, desc_var, elig_var = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
        left = tk.Frame(det.body, bg=CARD)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, textvariable=ttl_var, bg=CARD, font=self.f["h2"], anchor="w").pack(fill="x")
        tk.Label(left, textvariable=meta_var, bg=CARD, fg=MUTED, justify="left", anchor="w", wraplength=px(760)).pack(fill="x", pady=(px(2), px(0)))
        tk.Label(left, textvariable=desc_var, bg=CARD, fg=TEXT, justify="left", anchor="w", wraplength=px(760)).pack(fill="x", pady=(px(4), px(0)))
        right = tk.Frame(det.body, bg=CARD)
        right.pack(side="right", padx=(px(16), px(0)))
        elig = tk.Label(right, textvariable=elig_var, bg=CARD, font=self.f["sbold"], padx=px(12), pady=px(6), wraplength=px(260), justify="left")
        elig.pack(anchor="e", pady=(px(0), px(8)))
        add_btn = ttk.Button(right, text="Add course", style="Accent.TButton")
        add_btn.pack(anchor="e")

        def selected_oid():
            sel = tree.selection()
            return int(sel[0]) if sel else None

        def on_select(_e=None):
            oid = selected_oid()
            if oid is None:
                ttl_var.set("Select a course")
                meta_var.set("Pick a row to see its schedule, exam date and whether you can register.")
                desc_var.set("")
                elig_var.set("")
                elig.config(bg=CARD)
                add_btn.state(["disabled"])
                return
            o = offering(self.conn, oid)
            ttl_var.set(f"{o['code']}  -  {o['name']}")
            meta_var.set(f"{o['credits']} AKTS   |   Semester {o['level']}   |   {o['term']}   |   {o['instructor']}   |   Room {o['room']}\n"
                         f"Schedule: {format_slots(self.conn, oid)}   |   Exam: {format_exam(o)}   |   "
                         f"Prerequisite: {o['prereq'] or 'none'}   |   Waitlist: {waitlist_len(self.conn, oid)}"
                         + ("   |   Seats grow automatically with the waitlist" if (get_rule(self.conn, oid) and get_rule(self.conn, oid)["enabled"]) else ""))
            desc_var.set(o["description"] or "")
            reason = check_eligibility(self.conn, self.user["id"], oid)
            if on_waitlist(self.conn, self.user["id"], oid):
                reason = "You are on the waitlist for this course."
            if reason:
                elig_var.set(reason)
                elig.config(bg=CHIP["Full"][0], fg=CHIP["Full"][1])
                add_btn.config(text="Add course")
                add_btn.state(["disabled"])
            elif seats_taken(self.conn, oid) >= o["capacity"]:
                elig_var.set("Course is full - you can join the waitlist")
                elig.config(bg=CHIP["Waitlisted"][0], fg=CHIP["Waitlisted"][1])
                add_btn.config(text="Join waitlist")
                add_btn.state(["!disabled"])
            else:
                elig_var.set("You can add this course")
                elig.config(bg=CHIP["Approved"][0], fg=CHIP["Approved"][1])
                add_btn.config(text="Add course")
                add_btn.state(["!disabled"])

        def on_add(_e=None):
            oid = selected_oid()
            if oid is None:
                return
            status, msg, warns = register(self.conn, self.user["id"], oid)
            if status == "ok":
                self.flash(msg, "success")
                for w in warns:
                    self.flash(w, "warning")
            elif status == "full":
                if self.confirm("Course is full", msg + "\n\nJoin the waitlist?", "Join waitlist"):
                    note = join_waitlist(self.conn, self.user["id"], oid)
                    self.flash(note, "success" if enrollment_status(self.conn, self.user["id"], oid) else "info")
            else:
                self.flash(msg, "error")
            self.update_status()
            refresh()
            if tree.exists(str(oid)):
                tree.selection_set(str(oid))
                on_select()

        def refresh():
            tree.delete(*tree.get_children())
            lv = None if level_var.get() == "All" else int(level_var.get().split()[-1])
            passed = passed_set(transcript_rows(self.conn, self.user["id"]))
            for o in list_offerings(self.conn, term_var.get(), lv, search_var.get().strip()):
                st = status_label(self.conn, self.user["id"], o, passed)
                if hide_var.get() and st == "Passed":
                    continue
                self.add_row(tree, (o["code"], o["name"], o["credits"], o["level"], o["instructor"], format_slots(self.conn, o["id"]),
                                    f"{seats_taken(self.conn, o['id'])}/{o['capacity']}", o["prereq"] or "-", f"\u25cf  {st}"),
                             iid=str(o["id"]), tags=(st,) if st != "Open" else ())
            self.empty_state(tree, "No courses match your filters.")
            on_select()

        for v in (term_var, level_var, search_var, hide_var):
            v.trace_add("write", lambda *a: refresh())
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", on_add)
        tree.bind("<Return>", on_add)
        add_btn.config(command=on_add)
        return refresh

    def page_myreg(self, parent):
        top = self.card(parent)
        top.pack(fill="x", pady=(px(0), px(12)))
        step_box = tk.Frame(top.body, bg=CARD)
        step_box.pack(side="left")
        sum_box = tk.Frame(top.body, bg=CARD)
        sum_box.pack(side="right")
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(px(0), px(10)))
        cols = [("code", "CODE", 80), ("name", "COURSE", 240), ("cr", "AKTS", 55), ("sched", "SCHEDULE", 220),
                ("room", "ROOM", 70), ("status", "STATUS", 130)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        self.status_tags(tree)

        def refresh():
            tree.delete(*tree.get_children())
            sid = self.user["id"]
            regs = my_regs(self.conn, sid)
            for r in regs:
                self.add_row(tree, (r["code"], r["name"], r["credits"], format_slots(self.conn, r["id"]), r["room"],
                                    f"\u25cf  {r['status']}"), iid=f"R{r['id']}", tags=(r["status"],))
            for r, pos in my_waitlist(self.conn, sid):
                self.add_row(tree, (r["code"], r["name"], r["credits"], format_slots(self.conn, r["id"]), r["room"],
                                    f"\u25cf  Waitlist #{pos}"), iid=f"W{r['id']}", tags=("Waitlisted",))
            self.empty_state(tree, "You have not registered for any courses yet.")
            limit = credit_limit_from_rows(transcript_rows(self.conn, sid))
            used = sum(r["credits"] for r in regs)
            clear(step_box)
            self.stepper(step_box, reg_steps(regs)).pack()
            clear(sum_box)
            n = lambda s: sum(1 for r in regs if r["status"] == s)
            tk.Label(sum_box, text=f"AKTS  {used} / {limit}", bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(side="left", padx=(px(0), px(8)))
            self.meter(sum_box, used, limit, 120).pack(side="left", padx=(px(0), px(14)))
            tk.Label(sum_box, text=f"Draft {n('Draft')}   Pending {n('Pending')}   Approved {n('Approved')}", bg=CARD, fg=MUTED).pack(side="left")

        def on_submit():
            status, msg = submit_registration(self.conn, self.user["id"])
            self.flash(msg, "success" if status == "ok" else "error")
            self.update_status()
            refresh()

        def on_drop():
            sel = tree.selection()
            if not sel:
                self.flash("Select a course first.", "info")
                return
            kind, oid = sel[0][0], int(sel[0][1:])
            code = tree.item(sel[0], "values")[0]
            if kind == "R":
                if self.confirm("Drop course", f"Drop {code}? You can add it again while registration is open.", "Drop course", danger=True):
                    status, msg = drop(self.conn, self.user["id"], oid)
                    self.flash(msg, "success" if status == "ok" else "error")
            elif self.confirm("Leave waitlist", f"Leave the waitlist for {code}?", "Leave waitlist"):
                leave_waitlist(self.conn, self.user["id"], oid)
                self.flash(f"You left the waitlist for {code}.", "info")
            self.update_status()
            refresh()

        ttk.Button(bar, text="Submit for advisor approval", style="Accent.TButton", command=on_submit).pack(side="left")
        ttk.Button(bar, text="Drop course / leave waitlist", command=on_drop).pack(side="left", padx=px(10))
        return refresh

    def page_sched(self, parent):
        box = self.card(parent)
        box.pack(fill="both", expand=True)
        cv = tk.Canvas(box.body, bg=CARD, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        blocks = []

        def draw(_e=None):
            try:
                cv.delete("all")
                w, h = cv.winfo_width(), cv.winfo_height()
                if w < px(120) or h < px(120):
                    return
                left, top, right, bottom = px(58), px(34), px(6), px(6)
                colw, rowh = (w - left - right) / len(DAYS), (h - top - bottom) / len(HOURS)
                for d, name in enumerate(DAYS):
                    cv.create_text(left + colw * d + colw / 2, top / 2, text=name, font=self.f["bold"], fill=TEXT)
                for i, hh in enumerate(HOURS):
                    y = top + i * rowh
                    cv.create_line(left, y, w - right, y, fill="#eef0f4")
                    cv.create_text(left - px(10), y + px(4), text=f"{hh:02d}:00", anchor="ne", font=self.f["small"], fill=MUTED)
                for d in range(len(DAYS) + 1):
                    x = left + colw * d
                    cv.create_line(x, top, x, h - bottom, fill="#eef0f4")
                for code, room, status, day, s, e, (fill, edge) in blocks:
                    x0, x1 = left + colw * day + px(3), left + colw * (day + 1) - px(3)
                    y0, y1 = top + (s - HOURS[0]) * rowh + px(2), top + (e - HOURS[0]) * rowh - px(2)
                    cv.create_rectangle(x0, y0, x1, y1, fill=fill, outline=edge)
                    cv.create_rectangle(x0, y0, x0 + px(4), y1, fill=edge, outline=edge)
                    cv.create_text(x0 + px(12), y0 + px(6), text=code, anchor="nw", font=self.f["bold"], fill=TEXT)
                    cv.create_text(x0 + px(12), y0 + px(26), text=f"{room}  |  {status}", anchor="nw", font=self.f["small"], fill=TEXT,
                                   width=max(px(40), colw - px(22)))
                if not blocks:
                    cv.create_text(w / 2, h / 2, text="No courses registered for this term yet.", fill=MUTED, font=self.f["h2"])
            except tk.TclError:
                pass

        cv.bind("<Configure>", draw)

        def refresh():
            blocks.clear()
            for i, r in enumerate(my_regs(self.conn, self.user["id"])):
                for s in offering_slots(self.conn, r["id"]):
                    blocks.append((r["code"], r["room"], r["status"], s["day"], s["start_hour"], s["end_hour"],
                                   COURSE_COLORS[i % len(COURSE_COLORS)]))
            draw()
            cv.after(80, draw)
        return refresh

    def page_exams(self, parent):
        cols = [("date", "DATE", 110), ("time", "TIME", 70), ("code", "CODE", 80), ("name", "COURSE", 260),
                ("room", "ROOM", 80), ("note", "NOTE", 260)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        tree.tag_configure("clash", background="#fee2e2")

        def refresh():
            tree.delete(*tree.get_children())
            regs = [r for r in my_regs(self.conn, self.user["id"]) if r["exam_date"]]
            regs.sort(key=lambda r: (r["exam_date"], r["exam_hour"]))
            for r in regs:
                clash = [x["code"] for x in regs if x["id"] != r["id"] and (x["exam_date"], x["exam_hour"]) == (r["exam_date"], r["exam_hour"])]
                self.add_row(tree, (r["exam_date"], f"{r['exam_hour']:02d}:00", r["code"], r["name"], r["room"],
                                    f"CLASH with {', '.join(clash)}" if clash else ""), tags=("clash",) if clash else ())
            self.empty_state(tree, "No exams yet. Register for courses first.")
        return refresh

    def page_trans(self, parent):
        stats = tk.Frame(parent, bg=BG)
        stats.pack(fill="x", pady=(px(0), px(12)))
        cols = [("code", "CODE", 80), ("name", "COURSE", 260), ("cr", "AKTS", 70), ("grade", "GRADE", 70), ("pts", "POINTS", 70)]
        wrap, tree = self.mk_tree(parent, cols, tree_mode=True)
        tree.heading("#0", text="TERM", anchor="w")
        tree.column("#0", width=300)
        tree.tag_configure("fail", foreground="#b91c1c")
        wrap.pack(fill="both", expand=True)

        def refresh():
            fill_transcript(tree, self.conn, self.user["id"])
            clear(stats)
            rows = transcript_rows(self.conn, self.user["id"])
            g = cgpa(rows)
            self.stat_row(stats, [("CGPA", "-" if g is None else f"{g:.2f}", "cumulative", "#10b981"),
                                  ("Earned AKTS", str(earned_credits(rows)), "passed courses", "#f59e0b"),
                                  ("Terms completed", str(len({r["term"] for r in rows})), "", ACCENT)]).pack(fill="x")
        return refresh

    def page_notif(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(px(0), px(10)))
        cols = [("when", "WHEN", 130), ("kind", "TYPE", 90), ("msg", "MESSAGE", 820)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        tree.tag_configure("unread", font=self.f["bold"])

        def refresh():
            tree.delete(*tree.get_children())
            for n in all_notifications(self.conn, self.user["id"]):
                self.add_row(tree, (n["created"], f"\u25cf  {n['kind']}", n["message"]), tags=() if n["seen"] else ("unread",))
            self.empty_state(tree, "No notifications yet.")
            self.update_badge()

        def read_all():
            mark_all_seen(self.conn, self.user["id"])
            clear(self.banner_box)
            refresh()
        ttk.Button(bar, text="Mark all as read", command=read_all).pack(side="left")
        return refresh

    def page_acct(self, parent):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", anchor="n")
        prof = self.card(row)
        prof.pack(side="left", fill="y", padx=(px(0), px(14)))
        info = tk.StringVar()
        pw = self.card(row)
        pw.pack(side="left", fill="y")
        tk.Label(pw.body, text="Change password", bg=CARD, font=self.f["h2"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(px(0), px(10)))
        vars_ = [tk.StringVar() for _ in range(3)]
        for i, label in enumerate(("Current password", "New password", "Repeat new password"), start=1):
            tk.Label(pw.body, text=label, bg=CARD, fg=MUTED, font=self.f["sbold"]).grid(row=i * 2 - 1, column=0, columnspan=2, sticky="w", pady=(px(6), px(2)))
            ttk.Entry(pw.body, textvariable=vars_[i - 1], show="*", width=32).grid(row=i * 2, column=0, columnspan=2, sticky="ew")

        def change():
            ok, msg = change_password(self.conn, self.user["id"], *[v.get() for v in vars_])
            self.flash(msg, "success" if ok else "error")
            if ok:
                for v in vars_:
                    v.set("")
        ttk.Button(pw.body, text="Change password", style="Accent.TButton", command=change).grid(row=7, column=0, columnspan=2, sticky="e", pady=(px(16), px(0)))

        def refresh():
            clear(prof.body)
            self.avatar(prof.body, self.user["name"], 64).pack(anchor="w")
            tk.Label(prof.body, text=self.user["name"], bg=CARD, font=self.f["h2"]).pack(anchor="w", pady=(px(10), px(0)))
            tk.Label(prof.body, text=self.user["role"].title(), bg=CARD, fg=MUTED).pack(anchor="w")
            lines = [("Number", self.user["id"])]
            if self.user["department"]:
                lines.append(("Department", self.user["department"]))
            if self.user["advisor_id"]:
                lines.append(("Advisor", user_name(self.conn, self.user["advisor_id"])))
            for k, v in lines:
                tk.Label(prof.body, text=k.upper(), bg=CARD, fg=MUTED, font=self.f["sbold"]).pack(anchor="w", pady=(px(12), px(0)))
                tk.Label(prof.body, text=v, bg=CARD).pack(anchor="w")
        return refresh

    # ======================= ADVISOR PAGES =======================
    def page_approvals(self, parent):
        stats = tk.Frame(parent, bg=BG)
        stats.pack(fill="x", pady=(px(0), px(12)))
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(px(0), px(10)))
        info = tk.StringVar()
        cols = [("sid", "STUDENT NO", 100), ("sname", "STUDENT", 150), ("code", "CODE", 70), ("name", "COURSE", 230),
                ("cr", "AKTS", 55), ("sched", "SCHEDULE", 200), ("term", "TERM", 100)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        det = self.card(parent, ACCENT)
        det.pack(fill="x", pady=(px(12), px(0)))
        tk.Label(det.body, textvariable=info, bg=CARD, fg=TEXT, anchor="w", justify="left").pack(fill="x")

        def sel_pair():
            sel = tree.selection()
            if not sel:
                self.flash("Select a request first.", "info")
                return None
            sid, oid = sel[0].split("|")
            return sid, int(oid)

        def on_select(_e=None):
            sel = tree.selection()
            if not sel:
                info.set("Select a request to see the student's academic standing.")
                return
            sid = sel[0].split("|")[0]
            rows = transcript_rows(self.conn, sid)
            g = cgpa(rows)
            info.set(f"{user_name(self.conn, sid)}   |   CGPA {'-' if g is None else f'{g:.2f}'}   |   earned {earned_credits(rows)} AKTS   |   "
                     f"registered this term {reg_credits(self.conn, sid)} / {credit_limit_from_rows(rows)} AKTS")

        def refresh():
            tree.delete(*tree.get_children())
            pend = pending_for_advisor(self.conn, self.user["id"])
            for r in pend:
                self.add_row(tree, (r["student_id"], r["sname"], r["code"], r["name"], r["credits"],
                                    format_slots(self.conn, r["offering_id"]), r["term"]), iid=f"{r['student_id']}|{r['offering_id']}")
            self.empty_state(tree, "No pending requests. You are all caught up!")
            clear(stats)
            self.stat_row(stats, [("Pending requests", str(len(pend)), "waiting for you", "#f59e0b"),
                                  ("Students waiting", str(len({r["student_id"] for r in pend})), "", ACCENT)]).pack(fill="x")
            on_select()

        def approve_sel():
            p = sel_pair()
            if p and approve(self.conn, self.user["id"], *p):
                self.flash("Approved.", "success")
            refresh()

        def approve_student():
            p = sel_pair()
            if p:
                n = sum(approve(self.conn, self.user["id"], p[0], r["offering_id"])
                        for r in pending_for_advisor(self.conn, self.user["id"]) if r["student_id"] == p[0])
                self.flash(f"Approved {n} course(s) for {user_name(self.conn, p[0])}.", "success")
            refresh()

        def reject_sel():
            p = sel_pair()
            if p:
                reason = self.ask_text("Reject request", "Tell the student why. They will see this message.", "Reason", confirm="Reject", danger=True)
                if reason is not None:
                    reject(self.conn, self.user["id"], p[0], p[1], reason.strip())
                    self.flash("Rejected. The student was notified.", "warning")
            refresh()

        ttk.Button(bar, text="Approve selected", style="Accent.TButton", command=approve_sel).pack(side="left")
        ttk.Button(bar, text="Approve all for this student", command=approve_student).pack(side="left", padx=px(10))
        ttk.Button(bar, text="Reject selected", style="Danger.TButton", command=reject_sel).pack(side="left")
        tree.bind("<<TreeviewSelect>>", on_select)
        return refresh

    def page_autocap(self, parent):
        info = self.card(parent, ACCENT)
        info.pack(fill="x", pady=(0, 12))
        tk.Label(info.body, text="Grow seats automatically", bg=CARD, font=self.f["h2"]).pack(anchor="w")
        tk.Label(info.body, bg=CARD, fg=MUTED, justify="left", wraplength=px(900),
                 text="Pick a course and set a rule. When that many students are on its waitlist, the course gets extra seats right away "
                      "(never above your maximum), the waitlisted students are enrolled automatically and you get a notification. "
                      "Rules apply to everyone; the advisor who saved a rule last receives its alerts.").pack(anchor="w", pady=(4, 0))
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(0, 10))
        cols = [("code", "CODE", 70), ("name", "COURSE", 210), ("seats", "SEATS", 80), ("wait", "WAITLIST", 80),
                ("rule", "AUTOMATIC RULE", 330), ("by", "SET BY", 110)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        tree.tag_configure("ruleon", background=CHIP["Approved"][0])
        tree.tag_configure("ruleoff", foreground="#9ca3af")

        def refresh():
            keep = tree.selection()
            tree.delete(*tree.get_children())
            for o in list_offerings(self.conn, get_setting(self.conn, "current_term")):
                r = get_rule(self.conn, o["id"])
                tags = () if r is None else (("ruleon",) if r["enabled"] else ("ruleoff",))
                self.add_row(tree, (o["code"], o["name"], f"{seats_taken(self.conn, o['id'])}/{o['capacity']}",
                                    waitlist_len(self.conn, o["id"]), rule_text(r), user_name(self.conn, r["advisor_id"]) if r else ""),
                             iid=str(o["id"]), tags=tags)
            self.empty_state(tree, "No offerings for the current term.")
            if keep and tree.exists(keep[0]):
                tree.selection_set(keep[0])

        def sel_oid():
            sel = tree.selection()
            if not sel:
                self.flash("Select a course first.", "info")
                return None
            return int(sel[0])

        def set_rule_dialog():
            oid = sel_oid()
            if oid is None:
                return
            o, r = offering(self.conn, oid), get_rule(self.conn, oid)

            def check(v):
                try:
                    t, a, m = (int(x) for x in v)
                except ValueError:
                    return "Please enter whole numbers in every field."
                if t < 1 or a < 1:
                    return "Waitlist size and seats to add must be at least 1."
                if m <= o["capacity"]:
                    return f"Maximum capacity must be above the current capacity ({o['capacity']})."
                return None
            vals = self.ask_fields(f"Auto-capacity for {o['code']}",
                                   f"Now {seats_taken(self.conn, oid)}/{o['capacity']} seats and {waitlist_len(self.conn, oid)} waiting.",
                                   [("Add seats when the waitlist reaches", str(r["threshold"] if r else 3)),
                                    ("Seats to add each time", str(r["increase"] if r else 5)),
                                    ("Maximum capacity", str(r["max_capacity"] if r else o["capacity"] + 20))],
                                   "Save rule", check)
            if vals:
                ok, msg = set_rule(self.conn, self.user["id"], oid, *[int(x) for x in vals])
                self.flash(msg, "success" if ok else "error")
                refresh()

        def toggle():
            oid = sel_oid()
            if oid is not None:
                ok, msg = toggle_rule(self.conn, self.user["id"], oid)
                self.flash(msg, "success" if ok else "error")
                refresh()

        def remove():
            oid = sel_oid()
            if oid is None:
                return
            code = offering(self.conn, oid)["code"]
            if get_rule(self.conn, oid) is None:
                self.flash("This course has no rule.", "info")
            elif self.confirm("Remove rule", f"Stop growing {code} automatically?", "Remove rule", danger=True):
                delete_rule(self.conn, oid)
                self.flash(f"Rule removed for {code}.", "success")
            refresh()

        ttk.Button(bar, text="Set rule", style="Accent.TButton", command=set_rule_dialog).pack(side="left")
        ttk.Button(bar, text="Turn on / off", command=toggle).pack(side="left", padx=10)
        ttk.Button(bar, text="Remove rule", command=remove).pack(side="left")
        tree.bind("<Double-1>", lambda e: set_rule_dialog())
        return refresh

    def open_transcript(self, sid):
        win, body, foot, sub = self.popup(user_name(self.conn, sid), "", 800, 580)
        cols = [("code", "CODE", 80), ("name", "COURSE", 240), ("cr", "AKTS", 60), ("grade", "GRADE", 60), ("pts", "POINTS", 60)]
        wrap, tree = self.mk_tree(body, cols, tree_mode=True)
        tree.heading("#0", text="TERM", anchor="w")
        tree.column("#0", width=px(260))
        tree.tag_configure("fail", foreground="#b91c1c")
        wrap.pack(fill="both", expand=True)
        sub.config(text=fill_transcript(tree, self.conn, sid))

    def page_advisees(self, parent):
        stats = tk.Frame(parent, bg=BG)
        stats.pack(fill="x", pady=(px(0), px(12)))
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(px(0), px(10)))
        cols = [("id", "STUDENT NO", 100), ("name", "NAME", 170), ("cgpa", "CGPA", 60), ("earned", "EARNED AKTS", 100),
                ("term", "AKTS THIS TERM", 120), ("state", "REGISTRATION STATE", 260)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            mine = advisees(self.conn, self.user["id"])
            for s in mine:
                rows = transcript_rows(self.conn, s["id"])
                g = cgpa(rows)
                regs = my_regs(self.conn, s["id"])
                state = ", ".join(f"{sum(1 for r in regs if r['status'] == st)} {st.lower()}" for st in ("Draft", "Pending", "Approved")
                                  if any(r["status"] == st for r in regs)) or "nothing registered"
                self.add_row(tree, (s["id"], s["name"], "-" if g is None else f"{g:.2f}", earned_credits(rows),
                                    f"{sum(r['credits'] for r in regs)} / {credit_limit_from_rows(rows)}", state), iid=s["id"])
            self.empty_state(tree, "No advisees are assigned to you.")
            clear(stats)
            self.stat_row(stats, [("Advisees", str(len(mine)), "assigned to you", ACCENT),
                                  ("Pending approvals", str(len(pending_for_advisor(self.conn, self.user["id"]))), "", "#f59e0b")]).pack(fill="x")

        def view():
            sel = tree.selection()
            if sel:
                self.open_transcript(sel[0])
        ttk.Button(bar, text="View transcript", style="Accent.TButton", command=view).pack(side="left")
        tree.bind("<Double-1>", lambda e: view())
        return refresh

    # ======================= ADMIN PAGES =======================
    def page_offerings(self, parent):
        term_var = tk.StringVar(value=get_setting(self.conn, "current_term"))
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", pady=(px(0), px(10)))
        tk.Label(top, text="TERM", bg=BG, fg=MUTED, font=self.f["sbold"]).pack(side="left", padx=(px(0), px(8)))
        ttk.Combobox(top, textvariable=term_var, values=["All"] + terms_with_offerings(self.conn), state="readonly", width=14).pack(side="left")
        cols = [("code", "CODE", 70), ("name", "COURSE", 210), ("term", "TERM", 100), ("inst", "INSTRUCTOR", 95),
                ("seats", "SEATS", 70), ("wait", "WAITLIST", 70), ("auto", "AUTO-GROW", 250), ("sched", "SCHEDULE", 190)]
        wrap, tree = self.mk_tree(parent, cols)
        wrap.pack(fill="both", expand=True)
        tree.tag_configure("full", foreground="#b91c1c")

        def refresh():
            keep = tree.selection()
            tree.delete(*tree.get_children())
            for o in list_offerings(self.conn, None if term_var.get() == "All" else term_var.get()):
                full = seats_taken(self.conn, o["id"]) >= o["capacity"]
                self.add_row(tree, (o["code"], o["name"], o["term"], o["instructor"], f"{seats_taken(self.conn, o['id'])}/{o['capacity']}",
                                    waitlist_len(self.conn, o["id"]), rule_text(get_rule(self.conn, o["id"])), format_slots(self.conn, o["id"])),
                             iid=str(o["id"]), tags=("full",) if full else ())
            self.empty_state(tree, "No offerings for this term.")
            if keep and tree.exists(keep[0]):
                tree.selection_set(keep[0])

        def sel_oid():
            sel = tree.selection()
            if not sel:
                self.flash("Select an offering first.", "info")
                return None
            return int(sel[0])

        def change_cap():
            oid = sel_oid()
            if oid is None:
                return
            o = offering(self.conn, oid)
            cap = self.ask_int("Change capacity", f"New capacity for {o['code']} ({seats_taken(self.conn, oid)} seats are taken):", o["capacity"], 1, 500)
            if cap:
                n = set_capacity(self.conn, oid, cap)
                self.flash(f"Capacity of {o['code']} is now {cap}." + (f" {n} student(s) moved in from the waitlist." if n else ""), "success")
                refresh()

        def view_roster():
            oid = sel_oid()
            if oid is None:
                return
            o = offering(self.conn, oid)
            win, body, foot, sub = self.popup(f"{o['code']} - {o['name']}", f"{o['term']}   |   {seats_taken(self.conn, oid)}/{o['capacity']} seats",
                                              620, 480)
            wrap2, rt = self.mk_tree(body, [("id", "STUDENT NO", 100), ("name", "NAME", 220), ("status", "STATUS", 100)])
            wrap2.pack(fill="both", expand=True)

            def fill():
                rt.delete(*rt.get_children())
                for r in roster(self.conn, oid):
                    self.add_row(rt, (r["id"], r["name"], r["status"]), iid=r["id"])

            def remove():
                sel = rt.selection()
                if sel and self.confirm("Remove student", f"Remove {user_name(self.conn, sel[0])} from {o['code']}?", "Remove", danger=True, parent=win):
                    admin_remove(self.conn, sel[0], oid)
                    fill()
                    self.empty_state(rt, "Nobody is registered yet.")
                    sub.config(text=f"{o['term']}   |   {seats_taken(self.conn, oid)}/{o['capacity']} seats")
                    refresh()
            ttk.Button(foot, text="Remove selected student", style="Danger.TButton", command=remove).pack(side="left")
            fill()
            self.empty_state(rt, "Nobody is registered yet.")

        ttk.Button(top, text="Change capacity", style="Accent.TButton", command=change_cap).pack(side="right")
        ttk.Button(top, text="View roster", command=view_roster).pack(side="right", padx=px(10))
        term_var.trace_add("write", lambda *a: refresh())
        return refresh

    def page_control(self, parent):
        def refresh():
            clear(parent)
            cur, is_open = get_setting(self.conn, "current_term"), reg_is_open(self.conn)
            n_students = self.conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
            n_enr = self.conn.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0]
            n_wait = self.conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
            self.stat_row(parent, [("Current term", cur, "", ACCENT), ("Students", str(n_students), "", "#8b5cf6"),
                                   ("Registrations", str(n_enr), "", "#10b981"), ("On waitlists", str(n_wait), "", "#f59e0b")]).pack(fill="x")
            c = self.card(parent)
            c.pack(fill="x", pady=(px(14), px(0)))
            tk.Label(c.body, text="Registration window", bg=CARD, font=self.f["h2"]).pack(anchor="w")
            self.chip(c.body, "OPEN" if is_open else "CLOSED", *(CHIP["Approved"] if is_open else CHIP["Full"])).pack(anchor="w", pady=(px(8), px(6)))
            tk.Label(c.body, text="While closed, students cannot add, drop or submit courses.", bg=CARD, fg=MUTED).pack(anchor="w")
            ttk.Button(c.body, text="Close registration" if is_open else "Open registration", style="Accent.TButton",
                       command=toggle).pack(anchor="w", pady=(px(12), px(0)))
            d = self.card(parent, "#dc2626")
            d.pack(fill="x", pady=(px(14), px(0)))
            tk.Label(d.body, text="End of term (demo)", bg=CARD, font=self.f["h2"]).pack(anchor="w")
            tk.Label(d.body, text="Gives random grades to approved courses, removes unfinished registrations and moves everyone to the next term.\n"
                                  "Use it to demo transcripts, GPA and prerequisites.", bg=CARD, fg=MUTED, justify="left").pack(anchor="w", pady=(px(4), px(0)))
            ttk.Button(d.body, text="Close term and post grades (demo)", style="Danger.TButton", command=end_term).pack(anchor="w", pady=(px(12), px(0)))

        def toggle():
            now_open = reg_is_open(self.conn)
            set_setting(self.conn, "reg_open", "0" if now_open else "1")
            self.conn.commit()
            self.flash("Registration closed." if now_open else "Registration opened.", "warning" if now_open else "success")
            self.update_status()
            refresh()

        def end_term():
            if self.confirm("Close term", "Post grades and start the next term?\nThis cannot be undone (use --reset to rebuild the demo data).", "Close term", danger=True):
                ok, msg = close_term(self.conn)
                self.flash(msg, "success" if ok else "error")
                self.update_status()
                refresh()
        return refresh

    def page_ann(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(px(0), px(10)))
        wrap, tree = self.mk_tree(parent, [("when", "POSTED", 130), ("title", "TITLE", 220), ("body", "MESSAGE", 560)])
        wrap.pack(fill="both", expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for a in get_announcements(self.conn, 100):
                self.add_row(tree, (a["created"], a["title"], a["body"]))
            self.empty_state(tree, "No announcements yet.")

        def new():
            vals = self.ask_fields("New announcement", "Students will see it on their dashboard and get a notification.",
                                   [("Title", ""), ("Message", "")], "Post",
                                   lambda v: None if v[0].strip() else "Please enter a title.")
            if vals is None:
                return
            add_announcement(self.conn, vals[0].strip(), vals[1].strip())
            self.flash("Announcement posted. Students were notified.", "success")
            refresh()
        ttk.Button(bar, text="New announcement", style="Accent.TButton", command=new).pack(side="left")
        return refresh


if __name__ == "__main__":
    if "--reset" in sys.argv and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()
    App().mainloop()
