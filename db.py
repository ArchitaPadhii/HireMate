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

        CREATE TABLE IF NOT EXISTS applications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id     INTEGER NOT NULL,
            seeker_id  INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (job_id, seeker_id),
            FOREIGN KEY (job_id) REFERENCES jobs(id),
            FOREIGN KEY (seeker_id) REFERENCES users(id)
        );

    """)
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


def get_jobs_by_recruiter(recruiter_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE recruiter_id = ? ORDER BY id DESC", (recruiter_id,)
    ).fetchall()
    conn.close()
    return rows


def delete_job(job_id, recruiter_id):
    """Delete a job, but only if it belongs to this recruiter. Returns True if deleted."""
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM jobs WHERE id = ? AND recruiter_id = ?", (job_id, recruiter_id)
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def search_jobs(query, page, per_page=10, location="", source=""):
    """Search jobs with optional location and source filters. Returns (rows, total)."""
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

def get_job(job_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row

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


def has_applied(job_id, seeker_id):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM applications WHERE job_id = ? AND seeker_id = ?",
                       (job_id, seeker_id)).fetchone()
    conn.close()
    return row is not None


def get_applicant_emails(job_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT u.email FROM applications a
           JOIN users u ON u.id = a.seeker_id WHERE a.job_id = ?""", (job_id,)
    ).fetchall()
    conn.close()
    return {r["email"] for r in rows}

def get_applied_job_ids(seeker_id):
    conn = get_connection()
    rows = conn.execute("SELECT job_id FROM applications WHERE seeker_id = ?",
                        (seeker_id,)).fetchall()
    conn.close()
    return {r["job_id"] for r in rows}