import sqlite3
import os
import hashlib
import secrets

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hiremate.db")


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row   # lets us use row["name"]
    return conn


def init_db():
    """Create the tables if they do not exist yet."""
    conn = get_connection()

    # Migration: the old jobs table (from Step 3) had no 'source' column.
    # It has no data yet, so we can safely drop it and create the new one.
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(jobs)")]
    if columns and "source" not in columns:
        conn.execute("DROP TABLE jobs")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS resumes (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            filename TEXT,
            text     TEXT,
            skills   TEXT,
            score    INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        -- source = 'recruiter' (posted on HireMate) or 'dataset' (imported from CSV)
        -- recruiter_id is empty (NULL) for dataset jobs
        CREATE TABLE IF NOT EXISTS jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            recruiter_id INTEGER,
            title        TEXT NOT NULL,
            company      TEXT,
            location     TEXT,
            skills       TEXT,
            experience   TEXT,
            description  TEXT,
            source       TEXT NOT NULL DEFAULT 'recruiter',
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (recruiter_id) REFERENCES users(id)
        );

        -- status = 'pending', 'accepted' or 'rejected'
        CREATE TABLE IF NOT EXISTS applications (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id        INTEGER NOT NULL,
            seeker_id     INTEGER NOT NULL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            status        TEXT NOT NULL DEFAULT 'pending',
            reply_message TEXT,
            replied_at    TEXT,
            UNIQUE (job_id, seeker_id),
            FOREIGN KEY (job_id) REFERENCES jobs(id),
            FOREIGN KEY (seeker_id) REFERENCES users(id)
        );
    """)

    # Migration: the applications table from Step 11 had no status / reply columns.
    app_columns = [row["name"] for row in conn.execute("PRAGMA table_info(applications)")]
    if "status" not in app_columns:
        conn.execute("ALTER TABLE applications ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
    if "reply_message" not in app_columns:
        conn.execute("ALTER TABLE applications ADD COLUMN reply_message TEXT")
    if "replied_at" not in app_columns:
        conn.execute("ALTER TABLE applications ADD COLUMN replied_at TEXT")

    conn.commit()
    conn.close()


# ---------- PASSWORDS ----------
def hash_password(password):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return salt + "$" + h


def verify_password(password, stored):
    salt, h = stored.split("$")
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return check == h


# ---------- USERS ----------
def create_user(name, email, password, role):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (name, email, hash_password(password), role),
        )
        conn.commit()
        return True, "Account created"
    except sqlite3.IntegrityError:
        return False, "This email is already registered"
    finally:
        conn.close()


def get_user_by_email(email):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


# ---------- RESUMES ----------
def save_resume(user_id, filename, text, skills="", score=None):
    """Keep only one resume per user: delete the old one and save the new one."""
    conn = get_connection()
    conn.execute("DELETE FROM resumes WHERE user_id = ?", (user_id,))
    conn.execute(
        "INSERT INTO resumes (user_id, filename, text, skills, score) VALUES (?, ?, ?, ?, ?)",
        (user_id, filename, text, skills, score),
    )
    conn.commit()
    conn.close()


def update_resume_analysis(user_id, skills, score):
    """Save the detected skills (comma separated text) and the score."""
    conn = get_connection()
    conn.execute(
        "UPDATE resumes SET skills = ?, score = ? WHERE user_id = ?",
        (skills, score, user_id),
    )
    conn.commit()
    conn.close()


def get_latest_resume(user_id):
    conn = get_connection()
    resume = conn.execute(
        "SELECT * FROM resumes WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    conn.close()
    return resume


def count_resumes():
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) AS c FROM resumes").fetchone()
    conn.close()
    return row["c"]


# ---------- JOBS ----------
def count_jobs(recruiter_id=None):
    """Count all jobs, or only the jobs posted by one recruiter."""
    conn = get_connection()
    if recruiter_id is None:
        row = conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE recruiter_id = ?", (recruiter_id,)
        ).fetchone()
    conn.close()
    return row["c"]


def create_job(recruiter_id, title, company, location, skills, experience, description):
    conn = get_connection()
    conn.execute(
        """INSERT INTO jobs
           (recruiter_id, title, company, location, skills, experience, description, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'recruiter')""",
        (recruiter_id, title, company, location, skills, experience, description),
    )
    conn.commit()
    conn.close()


def get_job(job_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row


def get_jobs_by_recruiter(recruiter_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE recruiter_id = ? ORDER BY id DESC", (recruiter_id,)
    ).fetchall()
    conn.close()
    return rows


def delete_job(job_id, recruiter_id):
    """Delete a job (and its applications), but only if it belongs to this recruiter.
    Returns True if deleted."""
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM jobs WHERE id = ? AND recruiter_id = ?", (job_id, recruiter_id)
    )
    deleted = cursor.rowcount > 0
    if deleted:
        conn.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()
    return deleted


def search_jobs(query, page, per_page=10, location="", source=""):
    """Search jobs with optional location and source filters. Returns (rows, total).
    Jobs posted by recruiters come first, then dataset jobs."""
    conn = get_connection()
    conditions = []
    params = []
    if query:
        like = "%" + query + "%"
        conditions.append("(title LIKE ? OR company LIKE ? OR location LIKE ? OR skills LIKE ?)")
        params += [like, like, like, like]
    if location:
        conditions.append("location LIKE ?")
        params.append("%" + location + "%")
    if source in ("recruiter", "dataset"):
        conditions.append("source = ?")
        params.append(source)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute("SELECT COUNT(*) AS c FROM jobs " + where, params).fetchone()["c"]
    rows = conn.execute(
        "SELECT * FROM jobs " + where +
        " ORDER BY (source = 'recruiter') DESC, id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()
    return rows, total


# ---------- APPLICATIONS ----------
def apply_to_job(job_id, seeker_id):
    """Returns True if applied, False if already applied."""
    conn = get_connection()
    try:
        conn.execute("INSERT INTO applications (job_id, seeker_id) VALUES (?, ?)",
                     (job_id, seeker_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def withdraw_application(job_id, seeker_id):
    """Remove an application, but only while it is still pending. Returns True if removed."""
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM applications WHERE job_id = ? AND seeker_id = ? AND status = 'pending'",
        (job_id, seeker_id),
    )
    conn.commit()
    removed = cursor.rowcount > 0
    conn.close()
    return removed


def has_applied(job_id, seeker_id):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM applications WHERE job_id = ? AND seeker_id = ?",
                       (job_id, seeker_id)).fetchone()
    conn.close()
    return row is not None


def get_application_statuses(seeker_id):
    """Returns {job_id: status} for every job this seeker applied to."""
    conn = get_connection()
    rows = conn.execute("SELECT job_id, status FROM applications WHERE seeker_id = ?",
                        (seeker_id,)).fetchall()
    conn.close()
    return {r["job_id"]: r["status"] for r in rows}


def get_applicant_emails(job_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT u.email FROM applications a
           JOIN users u ON u.id = a.seeker_id WHERE a.job_id = ?""", (job_id,)
    ).fetchall()
    conn.close()
    return {r["email"] for r in rows}


def get_seeker_applications(seeker_id):
    """All applications of one seeker, newest first, with the job details."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.id AS app_id, a.job_id, a.status, a.reply_message,
                  a.created_at, a.replied_at,
                  j.title, j.company, j.location, j.source
           FROM applications a JOIN jobs j ON j.id = a.job_id
           WHERE a.seeker_id = ?
           ORDER BY a.id DESC""", (seeker_id,)
    ).fetchall()
    conn.close()
    return rows


def get_applications_for_recruiter(recruiter_id):
    """All applications received on the jobs of one recruiter, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.id AS app_id, a.job_id, a.status, a.reply_message,
                  a.created_at, a.replied_at,
                  j.title AS job_title, j.company AS job_company,
                  u.name AS seeker_name, u.email AS seeker_email,
                  r.skills AS resume_skills, r.score AS resume_score,
                  r.text AS resume_text
           FROM applications a
           JOIN jobs j ON j.id = a.job_id
           JOIN users u ON u.id = a.seeker_id
           LEFT JOIN resumes r ON r.user_id = u.id
           WHERE j.recruiter_id = ?
           ORDER BY a.id DESC""", (recruiter_id,)
    ).fetchall()
    conn.close()
    return rows


def get_application_owner(app_id):
    """Who applied (seeker_id) and who owns the job (recruiter_id) for one application."""
    conn = get_connection()
    row = conn.execute(
        """SELECT a.seeker_id, j.recruiter_id
           FROM applications a JOIN jobs j ON j.id = a.job_id
           WHERE a.id = ?""", (app_id,)
    ).fetchone()
    conn.close()
    return row


def reply_to_application(app_id, recruiter_id, status, message):
    """Accept or reject a pending application of this recruiter's job.
    Returns True if the reply was saved."""
    conn = get_connection()
    cursor = conn.execute(
        """UPDATE applications
           SET status = ?, reply_message = ?, replied_at = CURRENT_TIMESTAMP
           WHERE id = ? AND status = 'pending'
             AND job_id IN (SELECT id FROM jobs WHERE recruiter_id = ?)""",
        (status, message, app_id, recruiter_id),
    )
    conn.commit()
    saved = cursor.rowcount > 0
    conn.close()
    return saved