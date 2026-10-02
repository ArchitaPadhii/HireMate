"""Import real jobs from a CSV file into the HireMate database.

Usage (from the project folder, with venv active):
    python import_jobs.py                          -> reads jobs_dataset.csv
    python import_jobs.py my_file.csv              -> reads my_file.csv
    python import_jobs.py my_file.csv 3000         -> imports only the first 3000 jobs
"""
import csv
import io
import os
import re
import sys

import db
import resume_utils

CSV_FILE = sys.argv[1] if len(sys.argv) > 1 else "jobs_dataset.csv"
MAX_ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else None

# Possible column names for each field (after cleaning: lowercase, letters and digits only)
COLUMN_OPTIONS = {
    "title":       ["jobrole", "jobtitle", "title", "job", "position", "role"],
    "company":     ["company", "companyname", "employer"],
    "location":    ["location", "joblocation", "city"],
    "experience":  ["jobexperience", "experience", "experiencerequired", "exp"],
    "description": ["skillsdescription", "jobdescription", "description", "jobdetails", "details"],
    "skills":      ["skills", "keyskills", "skillsrequired"],
}


def clean_header(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def clean_text(value):
    """Remove extra spaces and line breaks."""
    return " ".join((value or "").split())


def read_csv_text(path):
    """Read the file as text. Try UTF-8 first, then Latin-1."""
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=encoding, newline="") as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise SystemExit("Could not read the file. Please save it as a CSV (UTF-8) and try again.")


def main():
    if not os.path.isfile(CSV_FILE):
        raise SystemExit(
            f"File not found: {CSV_FILE}\n"
            "Download the dataset, rename it to jobs_dataset.csv and keep it next to server.py."
        )

    reader = csv.DictReader(io.StringIO(read_csv_text(CSV_FILE)))
    headers = reader.fieldnames or []
    print("Columns found in the file:", headers)

    # Match every field to a real column name from the file
    cleaned = {clean_header(h): h for h in headers}
    columns = {}
    for field, options in COLUMN_OPTIONS.items():
        columns[field] = next((cleaned[o] for o in options if o in cleaned), None)
    print("Using columns:", columns)

    if columns["title"] is None or (columns["description"] is None and columns["skills"] is None):
        raise SystemExit(
            "Could not find the job title and description/skills columns.\n"
            "Send me the column names printed above and I will adjust this script."
        )

    db.init_db()
    conn = db.get_connection()
    conn.execute("DELETE FROM jobs WHERE source = 'dataset'")   # makes the import safe to repeat

    batch = []
    seen = set()
    imported = 0
    duplicates = 0
    without_skills = 0

    def get(row, field):
        column = columns[field]
        return clean_text(row.get(column)) if column else ""

    for row in reader:
        if MAX_ROWS and imported >= MAX_ROWS:
            break

        title = get(row, "title")[:120]
        if not title:
            continue

        company = get(row, "company")[:120]
        location = get(row, "location")[:150]
        experience = get(row, "experience")[:40]
        description = (get(row, "description") + " " + get(row, "skills")).strip()[:4000]

        # Skip duplicate postings
        key = (title.lower(), company.lower(), location.lower(), description[:200].lower())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)

        # Find known skills in the title and description with the same function used for resumes
        skills = resume_utils.extract_skills(title + " " + description)
        if not skills:
            without_skills += 1

        batch.append((None, title, company, location, ",".join(skills),
                      experience, description, "dataset"))
        imported += 1

        if len(batch) >= 1000:
            conn.executemany(
                """INSERT INTO jobs
                   (recruiter_id, title, company, location, skills, experience, description, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", batch)
            conn.commit()
            batch = []
            print(f"  ...{imported} jobs imported")

    if batch:
        conn.executemany(
            """INSERT INTO jobs
               (recruiter_id, title, company, location, skills, experience, description, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", batch)
        conn.commit()
    conn.close()

    print()
    print(f"Done. Imported {imported} jobs.")
    print(f"Skipped {duplicates} duplicate jobs.")
    print(f"{without_skills} jobs had none of our known skills in the text (still imported).")


if __name__ == "__main__":
    main()