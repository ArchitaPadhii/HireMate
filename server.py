from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import json
import secrets
import urllib.parse
import db
import resume_utils
import matching

PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # 5 MB
JOBS_PER_PAGE = 10

CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
}

# Login sessions: {"random_token": user_id}
SESSIONS = {}

# Which page belongs to which role
SEEKER_PAGES = {
    "seeker_dashboard.html",
    "upload_resume.html",
    "resume_result.html",
    "browse_jobs.html",
    "recommended_jobs.html",
}
RECRUITER_PAGES = {
    "recruiter_dashboard.html",
    "post_job.html",
    "my_jobs.html",
    "ranked_candidates.html",
}
# Pages that any logged-in user (seeker or recruiter) can open
SHARED_PAGES = {"job_detail.html"}
AUTH_PAGES = {"login.html", "register.html"}


def dashboard_for(role):
    if role == "recruiter":
        return "/recruiter_dashboard.html"
    return "/seeker_dashboard.html"


def job_to_dict(row, full_description=False):
    """Convert a jobs table row into a dictionary that can be sent as JSON."""
    description = row["description"] or ""
    skills = [s for s in (row["skills"] or "").split(",") if s.strip()]
    job = {
        "id": row["id"],
        "title": row["title"],
        "company": row["company"] or "",
        "location": row["location"] or "",
        "experience": row["experience"] or "",
        "skills": skills,
        "source": row["source"],
        "created_at": row["created_at"],
    }
    if full_description:
        job["description"] = description
    else:
        job["snippet"] = description[:220] + ("..." if len(description) > 220 else "")
    return job


class HireMateHandler(BaseHTTPRequestHandler):

    # ---------- HELPERS ----------
    def send_json(self, data, status=200, cookie=None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def get_session_token(self):
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return part[len("session="):]
        return None

    def get_current_user(self):
        token = self.get_session_token()
        user_id = SESSIONS.get(token)
        if user_id is None:
            return None
        return db.get_user_by_id(user_id)

    def get_query_params(self):
        """Read ?a=1&b=2 from the URL into a dictionary."""
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        return {key: values[0] for key, values in params.items()}

    def serve_file(self, file_path):
        if not os.path.isfile(file_path):
            self.send_error(404, "Page not found")
            return

        ext = os.path.splitext(file_path)[1]
        content_type = CONTENT_TYPES.get(ext, "text/plain")

        with open(file_path, "rb") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]

        # ----- APIs -----
        if path == "/api/me":
            user = self.get_current_user()
            if user is None:
                self.send_json({"error": "Not logged in"}, 401)
            else:
                self.send_json({
                    "id": user["id"],
                    "name": user["name"],
                    "email": user["email"],
                    "role": user["role"],
                })
            return

        if path == "/api/seeker/stats":
            user = self.get_current_user()
            if user is None or user["role"] != "seeker":
                self.send_json({"error": "Not allowed"}, 403)
                return
            resume = db.get_latest_resume(user["id"])
            skills_count = 0
            if resume and resume["skills"]:
                skills_count = len([s for s in resume["skills"].split(",") if s.strip()])
            self.send_json({
                "resume_uploaded": resume is not None,
                "score": resume["score"] if resume else None,
                "skills_count": skills_count,
                "jobs_available": db.count_jobs(),
            })
            return

        if path == "/api/seeker/resume":
            user = self.get_current_user()
            if user is None or user["role"] != "seeker":
                self.send_json({"error": "Not allowed"}, 403)
                return
            resume = db.get_latest_resume(user["id"])
            if resume is None:
                self.send_json({"uploaded": False})
            else:
                text = resume["text"] or ""
                self.send_json({
                    "uploaded": True,
                    "filename": resume["filename"],
                    "chars": len(text),
                    "preview": text[:1500],
                })
            return

        if path == "/api/seeker/analysis":
            user = self.get_current_user()
            if user is None or user["role"] != "seeker":
                self.send_json({"error": "Not allowed"}, 403)
                return
            resume = db.get_latest_resume(user["id"])
            if resume is None:
                self.send_json({"uploaded": False})
                return

            # Always analyze the saved text again, so resumes uploaded
            # before this feature existed also work.
            analysis = resume_utils.analyze_resume(resume["text"] or "")
            db.update_resume_analysis(user["id"], ",".join(analysis["skills"]), analysis["score"])

            analysis["uploaded"] = True
            analysis["filename"] = resume["filename"]
            self.send_json(analysis)
            return

        # Search and browse all jobs (any logged-in user), with filters
        if path == "/api/jobs":
            user = self.get_current_user()
            if user is None:
                self.send_json({"error": "Not logged in"}, 401)
                return
            params = self.get_query_params()
            query = params.get("q", "").strip()[:100]
            location = params.get("location", "").strip()[:100]
            source = params.get("source", "").strip()
            try:
                page = max(1, int(params.get("page", "1")))
            except ValueError:
                page = 1

            rows, total = db.search_jobs(query, page, JOBS_PER_PAGE, location, source)
            pages = max(1, (total + JOBS_PER_PAGE - 1) // JOBS_PER_PAGE)
            self.send_json({
                "jobs": [job_to_dict(r) for r in rows],
                "total": total,
                "page": page,
                "pages": pages,
            })
            return

        # The jobs posted by the logged-in recruiter
        if path == "/api/recruiter/jobs":
            user = self.get_current_user()
            if user is None or user["role"] != "recruiter":
                self.send_json({"error": "Not allowed"}, 403)
                return
            rows = db.get_jobs_by_recruiter(user["id"])
            self.send_json({"jobs": [job_to_dict(r, full_description=True) for r in rows]})
            return

        if path == "/api/recruiter/stats":
            user = self.get_current_user()
            if user is None or user["role"] != "recruiter":
                self.send_json({"error": "Not allowed"}, 403)
                return
            self.send_json({
                "jobs_posted": db.count_jobs(user["id"]),
                "candidates": db.count_resumes(),
            })
            return

        # Step 9: best jobs for the logged-in seeker
        if path == "/api/seeker/recommended":
            user = self.get_current_user()
            if user is None or user["role"] != "seeker":
                self.send_json({"error": "Not allowed"}, 403)
                return
            resume = db.get_latest_resume(user["id"])
            if resume is None:
                self.send_json({"uploaded": False})
                return
            skills = [s for s in (resume["skills"] or "").split(",") if s.strip()]
            results = matching.recommend(resume["text"] or "", skills, 20)
            jobs = []
            for r in results:
                job = job_to_dict(r["row"])
                job["score"] = r["score"]
                job["matched"] = r["matched"]
                job["missing"] = r["missing"]
                jobs.append(job)
            self.send_json({"uploaded": True, "jobs": jobs})
            return

        # Step 10: ranked candidates for one job of the logged-in recruiter
        if path == "/api/recruiter/candidates":
            user = self.get_current_user()
            if user is None or user["role"] != "recruiter":
                self.send_json({"error": "Not allowed"}, 403)
                return
            try:
                job_id = int(self.get_query_params().get("job_id", "0"))
            except ValueError:
                job_id = 0
            job = db.get_job(job_id)
            if job is None or job["recruiter_id"] != user["id"]:
                self.send_json({"error": "Job not found"}, 404)
                return
            applied = db.get_applicant_emails(job_id)
            candidates = matching.rank_candidates(job_id)
            for c in candidates:
                c["applied"] = c["email"] in applied
            self.send_json({"job": job_to_dict(job), "candidates": candidates})
            return

        # Step 11: one job with full description (and match score for seekers)
        if path == "/api/job":
            user = self.get_current_user()
            if user is None:
                self.send_json({"error": "Not logged in"}, 401)
                return
            try:
                job_id = int(self.get_query_params().get("id", "0"))
            except ValueError:
                job_id = 0
            row = db.get_job(job_id)
            if row is None:
                self.send_json({"error": "Job not found"}, 404)
                return
            job = job_to_dict(row, full_description=True)
            job["role"] = user["role"]
            if user["role"] == "seeker":
                job["applied"] = db.has_applied(job_id, user["id"])
                resume = db.get_latest_resume(user["id"])
                if resume:
                    skills = [s for s in (resume["skills"] or "").split(",") if s.strip()]
                    job["match"] = matching.score_one(resume["text"] or "", skills, job_id)
            self.send_json(job)
            return

        # ----- PAGES -----
        if path == "/":
            path = "/index.html"

        if ".." in path:
            self.send_error(403)
            return

        page = path.lstrip("/")

        if page in SEEKER_PAGES or page in RECRUITER_PAGES:
            user = self.get_current_user()
            if user is None:
                self.redirect("/login.html")
                return
            needed_role = "seeker" if page in SEEKER_PAGES else "recruiter"
            if user["role"] != needed_role:
                self.redirect(dashboard_for(user["role"]))
                return

        if page in SHARED_PAGES:
            if self.get_current_user() is None:
                self.redirect("/login.html")
                return

        if page in AUTH_PAGES:
            user = self.get_current_user()
            if user is not None:
                self.redirect(dashboard_for(user["role"]))
                return

        if path.startswith("/static/"):
            file_path = os.path.join(BASE_DIR, path.lstrip("/"))
        else:
            file_path = os.path.join(BASE_DIR, "pages", page)

        self.serve_file(file_path)

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/register":
            self.handle_register()
        elif path == "/api/login":
            self.handle_login()
        elif path == "/api/logout":
            self.handle_logout()
        elif path == "/api/upload_resume":
            self.handle_upload_resume()
        elif path == "/api/recruiter/jobs":
            self.handle_create_job()
        elif path == "/api/recruiter/jobs/delete":
            self.handle_delete_job()
        elif path == "/api/apply":
            self.handle_apply()
        else:
            self.send_error(404)

    def handle_register(self):
        data = self.read_json()
        name = data.get("name", "").strip()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        role = data.get("role", "")

        if not name or not email or not password:
            self.send_json({"error": "All fields are required"}, 400)
            return
        if "@" not in email:
            self.send_json({"error": "Enter a valid email address"}, 400)
            return
        if len(password) < 6:
            self.send_json({"error": "Password must be at least 6 characters"}, 400)
            return
        if role not in ("seeker", "recruiter"):
            self.send_json({"error": "Please select a role"}, 400)
            return

        ok, message = db.create_user(name, email, password, role)
        if ok:
            self.send_json({"message": message})
        else:
            self.send_json({"error": message}, 400)

    def handle_login(self):
        data = self.read_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        user = db.get_user_by_email(email)
        if user is None or not db.verify_password(password, user["password_hash"]):
            self.send_json({"error": "Invalid email or password"}, 401)
            return

        token = secrets.token_hex(16)
        SESSIONS[token] = user["id"]
        cookie = f"session={token}; Path=/; HttpOnly"
        self.send_json({"message": "Login successful", "role": user["role"]}, cookie=cookie)

    def handle_logout(self):
        token = self.get_session_token()
        SESSIONS.pop(token, None)
        self.send_json({"message": "Logged out"}, cookie="session=; Path=/; Max-Age=0")

    def handle_upload_resume(self):
        user = self.get_current_user()
        if user is None or user["role"] != "seeker":
            self.send_json({"error": "Not allowed"}, 403)
            return

        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self.send_json({"error": "No file received"}, 400)
            return
        if length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "File is too large (max 5 MB)"}, 413)
            return

        pdf_bytes = self.rfile.read(length)

        # Every real PDF starts with "%PDF"
        if not pdf_bytes.startswith(b"%PDF"):
            self.send_json({"error": "Only PDF files are allowed"}, 400)
            return

        # The original file name is only used for display (we never save the file under this name)
        filename = urllib.parse.unquote(self.headers.get("X-Filename", "resume.pdf"))
        filename = os.path.basename(filename)[:100]

        try:
            text, pages = resume_utils.extract_text_from_pdf(pdf_bytes)
        except Exception:
            self.send_json({"error": "Could not read this PDF. Try another file."}, 400)
            return

        if len(text.strip()) < 50:
            self.send_json({
                "error": "No readable text found. Please upload a text-based PDF, not a scanned image."
            }, 400)
            return

        # Save the file as uploads/user<id>.pdf (an old file is overwritten)
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        with open(os.path.join(UPLOADS_DIR, f"user{user['id']}.pdf"), "wb") as f:
            f.write(pdf_bytes)

        # Analyze right away and save the score and skills
        analysis = resume_utils.analyze_resume(text)
        db.save_resume(user["id"], filename, text,
                       ",".join(analysis["skills"]), analysis["score"])

        self.send_json({
            "message": "Resume uploaded successfully",
            "filename": filename,
            "pages": pages,
            "chars": len(text),
            "score": analysis["score"],
        })

    def handle_create_job(self):
        user = self.get_current_user()
        if user is None or user["role"] != "recruiter":
            self.send_json({"error": "Not allowed"}, 403)
            return

        data = self.read_json()
        title = data.get("title", "").strip()
        company = data.get("company", "").strip()
        location = data.get("location", "").strip()
        experience = data.get("experience", "").strip() or "Not specified"
        raw_skills = data.get("skills", "")
        description = data.get("description", "").strip()

        if not title or not company or not location:
            self.send_json({"error": "Title, company and location are required"}, 400)
            return
        if len(title) > 100 or len(company) > 100 or len(location) > 100:
            self.send_json({"error": "Title, company and location must be under 100 characters"}, 400)
            return
        if len(description) < 20:
            self.send_json({"error": "Please write a job description (at least 20 characters)"}, 400)
            return
        if len(description) > 5000:
            self.send_json({"error": "Description is too long (max 5000 characters)"}, 400)
            return

        # Clean the typed skills and add the skills found in the description
        skills = resume_utils.normalize_skill_list(raw_skills, description)
        if len(skills) == 0:
            self.send_json({"error": "Please add at least one required skill"}, 400)
            return

        db.create_job(user["id"], title, company, location,
                      ",".join(skills), experience[:40], description)
        self.send_json({"message": "Job posted successfully", "skills": skills})

    def handle_delete_job(self):
        user = self.get_current_user()
        if user is None or user["role"] != "recruiter":
            self.send_json({"error": "Not allowed"}, 403)
            return

        data = self.read_json()
        try:
            job_id = int(data.get("id", 0))
        except (TypeError, ValueError):
            job_id = 0

        if db.delete_job(job_id, user["id"]):
            self.send_json({"message": "Job deleted"})
        else:
            self.send_json({"error": "Job not found"}, 404)

    # Step 11: a seeker applies to a job
    def handle_apply(self):
        user = self.get_current_user()
        if user is None or user["role"] != "seeker":
            self.send_json({"error": "Not allowed"}, 403)
            return
        if db.get_latest_resume(user["id"]) is None:
            self.send_json({"error": "Please upload your resume first"}, 400)
            return

        data = self.read_json()
        try:
            job_id = int(data.get("id", 0))
        except (TypeError, ValueError):
            job_id = 0
        if db.get_job(job_id) is None:
            self.send_json({"error": "Job not found"}, 404)
            return

        if db.apply_to_job(job_id, user["id"]):
            self.send_json({"message": "Applied successfully"})
        else:
            self.send_json({"error": "You have already applied to this job"}, 400)


if __name__ == "__main__":
    db.init_db()
    server = HTTPServer(("localhost", PORT), HireMateHandler)
    print(f"HireMate running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop")
    server.serve_forever()