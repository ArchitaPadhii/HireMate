"""matching.py - TF-IDF + cosine similarity, plus skill overlap."""
import math
import re
from collections import Counter

import db

STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or
that the this to with we you your will our their they can able etc using use work
experience years year required responsibilities""".split())

_cache = {"key": None, "idf": {}, "vectors": {}, "rows": {}}


def tokenize(text):
    words = re.findall(r"[a-z][a-z0-9+#]*", (text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _normalize(vec):
    norm = math.sqrt(sum(v * v for v in vec.values()))
    return {k: v / norm for k, v in vec.items()} if norm else {}


def _vectorize(text, idf):
    counts = Counter(tokenize(text))
    vec = {w: (1 + math.log(c)) * idf[w] for w, c in counts.items() if w in idf}
    return _normalize(vec)


def _cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _build_index():
    """Build TF-IDF vectors for all jobs. Rebuilt only when jobs change."""
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    conn.close()

    key = (len(rows), max((r["id"] for r in rows), default=0))
    if _cache["key"] == key:
        return

    docs = {}
    df = Counter()
    for r in rows:
        text = ((r["title"] or "") + " ") * 2 + (r["skills"] or "") + " " + (r["description"] or "")
        tokens = tokenize(text)
        docs[r["id"]] = tokens
        df.update(set(tokens))

    n = len(rows)
    idf = {w: math.log((1 + n) / (1 + d)) + 1 for w, d in df.items()}

    vectors = {}
    for job_id, tokens in docs.items():
        counts = Counter(tokens)
        vec = {w: (1 + math.log(c)) * idf[w] for w, c in counts.items()}
        vectors[job_id] = _normalize(vec)

    _cache.update(key=key, idf=idf, vectors=vectors, rows={r["id"]: r for r in rows})


def _score(resume_vec, resume_skills, job_id, min_skills=1):
    """Return (percent, matched_skills, missing_skills) for one resume-job pair.

    min_skills: a job with only 1-2 listed skills should not get a perfect skill score
    just because the resume has those 1-2 skills. The skill part is divided by at least
    this many skills (used for job seekers; recruiters use 1)."""
    row = _cache["rows"][job_id]
    job_skills = [s.strip() for s in (row["skills"] or "").split(",") if s.strip()]
    have = {s.lower() for s in resume_skills}

    matched = [s for s in job_skills if s.lower() in have]
    missing = [s for s in job_skills if s.lower() not in have]

    text_pct = min(1.0, _cosine(resume_vec, _cache["vectors"][job_id]) * 2.5)
    if job_skills:
        skill_pct = len(matched) / max(len(job_skills), min_skills)
        final = 0.6 * skill_pct + 0.4 * text_pct
    else:
        final = text_pct
    return round(final * 100), matched, missing


def recommend(resume_text, resume_skills, limit=20):
    """Best jobs for a resume. Returns a list of dicts, highest score first.

    Dataset jobs with fewer than 3 detected skills are skipped, because we cannot
    tell the seeker anything useful about them (no skills to match or to miss).
    Jobs posted by recruiters on HireMate are always included."""
    _build_index()
    vec = _vectorize(resume_text, _cache["idf"])
    results = []
    for job_id, row in _cache["rows"].items():
        job_skills = [s for s in (row["skills"] or "").split(",") if s.strip()]
        if len(job_skills) < 3 and row["source"] != "recruiter":
            continue
        score, matched, missing = _score(vec, resume_skills, job_id, min_skills=5)
        results.append({"row": row, "score": score,
                        "matched": matched, "missing": missing})
    results.sort(key=lambda r: (r["score"], r["row"]["source"] == "recruiter"), reverse=True)
    return results[:limit]


def rank_candidates(job_id, limit=50):
    """Best resumes for one job (for the recruiter). Highest score first."""
    _build_index()
    if job_id not in _cache["rows"]:
        return []
    conn = db.get_connection()
    resumes = conn.execute(
        """SELECT r.*, u.name AS user_name, u.email AS user_email
           FROM resumes r JOIN users u ON u.id = r.user_id"""
    ).fetchall()
    conn.close()

    results = []
    for r in resumes:
        skills = [s for s in (r["skills"] or "").split(",") if s.strip()]
        vec = _vectorize(r["text"], _cache["idf"])
        score, matched, missing = _score(vec, skills, job_id)
        results.append({"name": r["user_name"], "email": r["user_email"],
                        "score": score, "matched": matched, "missing": missing})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def score_one(resume_text, resume_skills, job_id, min_skills=5):
    """Match score for one resume and one job.
    Seekers use min_skills=5 (same as recommendations), recruiters pass 1."""
    _build_index()
    if job_id not in _cache["rows"]:
        return None
    vec = _vectorize(resume_text, _cache["idf"])
    score, matched, missing = _score(vec, resume_skills, job_id, min_skills)
    return {"score": score, "matched": matched, "missing": missing}