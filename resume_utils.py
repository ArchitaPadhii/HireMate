import io
import os
import re
from pypdf import PdfReader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------
# 1. Extract text from a PDF
# ---------------------------------------------------------------
def extract_text_from_pdf(pdf_bytes):
    """Read the text from PDF bytes. Returns (text, number_of_pages)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))

    pages_text = []
    for page in reader.pages:
        pages_text.append(page.extract_text() or "")

    text = "\n".join(pages_text)

    # Strip every line and remove empty lines
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    return text, len(reader.pages)


# ---------------------------------------------------------------
# 2. Extract skills
# ---------------------------------------------------------------
def load_skills():
    """Read the skill list from skills.txt (one skill per line)."""
    path = os.path.join(BASE_DIR, "skills.txt")
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


SKILLS = load_skills()

# Short forms: what is written in the resume -> the real skill name
ALIASES = {
    "sklearn": "scikit-learn",
    "ml": "Machine Learning",
    "nlp": "Natural Language Processing",
    "powerbi": "Power BI",
    "postgres": "PostgreSQL",
    "reactjs": "React",
    "nodejs": "Node.js",
}


def _norm(s):
    """Normalize text: lowercase, hyphens become spaces, extra spaces and
    line breaks are removed. This lets 'Data\nVisualization' and
    'data-cleaning' match 'Data Visualization' and 'Data Cleaning'."""
    return " ".join(s.lower().replace("-", " ").split())


def _find(term, text_norm):
    """Check whether 'term' appears in the text as a whole word.
    (So that 'Java' is not found inside 'JavaScript'.)"""
    pattern = r"(?<![a-z0-9+#])" + re.escape(_norm(term)) + r"(?![a-z0-9+#])"
    return re.search(pattern, text_norm) is not None


def extract_skills(text):
    """Return the list of known skills that appear in the resume text."""
    text_norm = _norm(text)
    found = []

    for skill in SKILLS:
        if _find(skill, text_norm):
            found.append(skill)

    for alias, skill in ALIASES.items():
        if skill not in found and _find(alias, text_norm):
            found.append(skill)

    return found


# ---------------------------------------------------------------
# 3. Resume score (out of 100)
# ---------------------------------------------------------------
SECTION_WORDS = {
    "Summary":        ["summary", "objective", "profile"],
    "Skills":         ["skills"],
    "Education":      ["education", "academic"],
    "Projects":       ["project"],
    "Experience":     ["experience", "internship", "work history", "employment"],
    "Certifications": ["certification", "achievement", "award", "courses"],
}

SOFT_SKILLS = {"Problem Solving", "Communication", "Teamwork", "Leadership"}


def find_sections(text):
    """Find heading-like short lines and report which sections are present."""
    present = {name: False for name in SECTION_WORDS}
    for line in text.splitlines():
        line_lower = line.strip().lower()
        if len(line_lower) > 45:        # a long line is not a heading
            continue
        for name, words in SECTION_WORDS.items():
            if any(w in line_lower for w in words):
                present[name] = True
    return present


def calculate_score(text, skills):
    """Return (total_score, breakdown). The breakdown lists points per part."""
    breakdown = []
    sections = find_sections(text)

    # a) Contact details: 10 points
    contact = 0
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        contact += 4
    if re.search(r"(\+?\d[\d\s-]{8,}\d)", text):
        contact += 3
    if "linkedin" in text.lower() or "github" in text.lower():
        contact += 3
    breakdown.append({"name": "Contact details", "points": contact, "max": 10})

    # b) Key sections: 30 points
    sec_points = 0
    for name, pts in [("Skills", 7), ("Education", 7), ("Projects", 8), ("Experience", 8)]:
        if sections[name]:
            sec_points += pts
    breakdown.append({"name": "Key sections", "points": sec_points, "max": 30})

    # c) Technical skills: 30 points (1 point per skill, soft skills are not counted)
    tech_count = len([s for s in skills if s not in SOFT_SKILLS])
    breakdown.append({"name": "Technical skills", "points": min(tech_count, 30), "max": 30})

    # d) Resume length: 10 points
    words = len(text.split())
    if 200 <= words <= 800:
        length_points = 10
    elif 100 <= words < 200 or 800 < words <= 1000:
        length_points = 5
    else:
        length_points = 0
    breakdown.append({"name": "Resume length", "points": length_points, "max": 10})

    # e) Extras: 20 points
    extra = 0
    if sections["Summary"]:
        extra += 5
    if sections["Certifications"]:
        extra += 5
    if re.search(r"\d+\s?%|\d[\d,]{2,}", text):   # numbers or percentages show measurable results
        extra += 10
    breakdown.append({"name": "Summary, certifications, results", "points": extra, "max": 20})

    total = sum(item["points"] for item in breakdown)
    return total, breakdown


def improvement_tips(text, skills):
    """Return simple suggestions based on what is missing from the resume."""
    tips = []
    sections = find_sections(text)
    if not sections["Summary"]:
        tips.append("Add a short professional summary at the top.")
    if not sections["Projects"]:
        tips.append("Add a Projects section with what you built and the tools you used.")
    if not sections["Experience"]:
        tips.append("Add internships or work experience, even small ones.")
    if not sections["Certifications"]:
        tips.append("Add certifications or achievements.")
    if not re.search(r"\d+\s?%|\d[\d,]{2,}", text):
        tips.append("Add numbers to your points (for example: improved speed by 40%).")
    tech_count = len([s for s in skills if s not in SOFT_SKILLS])
    if tech_count < 10:
        tips.append("List more technical skills that you actually know.")
    words = len(text.split())
    if words < 200:
        tips.append("Your resume is very short. Add more detail about your work.")
    if words > 800:
        tips.append("Your resume is long. Keep it focused, ideally one or two pages.")
    return tips


# ---------------------------------------------------------------
# 4. Roles and missing skills
# ---------------------------------------------------------------
ROLES = {
    "Data Analyst": [
        "Python", "SQL", "Excel", "Pandas", "NumPy", "Data Visualization",
        "Power BI", "Tableau", "Statistics", "Data Cleaning", "Matplotlib",
    ],
    "Python Developer": [
        "Python", "Django", "Flask", "REST API", "SQL", "Git",
        "Docker", "OOP", "Data Structures", "PostgreSQL",
    ],
    "Web Developer": [
        "HTML", "CSS", "JavaScript", "React", "Node.js", "Git",
        "REST API", "MongoDB", "Bootstrap", "SQL",
    ],
    "Machine Learning Engineer": [
        "Python", "Machine Learning", "Deep Learning", "scikit-learn", "TensorFlow",
        "Pandas", "NumPy", "PyTorch", "Docker", "Git",
    ],
    "Data Scientist": [
        "Python", "Machine Learning", "Statistics", "Pandas", "NumPy", "SQL",
        "Data Visualization", "scikit-learn", "Matplotlib", "Deep Learning",
    ],
    "Java Developer": [
        "Java", "Spring Boot", "SQL", "MySQL", "Git", "OOP",
        "Data Structures", "REST API", "Docker",
    ],
}


def role_matches(skills):
    """For every role: how many skills match and which ones are missing.
    The best matching role comes first."""
    have = set(skills)
    results = []
    for role, needed in ROLES.items():
        matched = [s for s in needed if s in have]
        missing = [s for s in needed if s not in have]
        results.append({
            "role": role,
            "match": round(len(matched) * 100 / len(needed)),
            "matched": matched,
            "missing": missing,
        })
    results.sort(key=lambda r: r["match"], reverse=True)
    return results


# ---------------------------------------------------------------
# 5. Run the full analysis
# ---------------------------------------------------------------
def analyze_resume(text):
    """Extract skills, calculate the score and match the resume with roles."""
    skills = extract_skills(text)
    score, breakdown = calculate_score(text, skills)
    return {
        "skills": skills,
        "score": score,
        "breakdown": breakdown,
        "word_count": len(text.split()),
        "tips": improvement_tips(text, skills),
        "roles": role_matches(skills),
    }
# ---------------------------------------------------------------
# 6. Clean the skills typed by a recruiter
# ---------------------------------------------------------------
def normalize_skill_list(raw_skills, description=""):
    """Turn 'python, sql ,Pandas' into ['Python', 'SQL', 'Pandas'].
    Skills found in the job description are added automatically."""
    canonical = {s.lower(): s for s in SKILLS}
    result = []
    seen = set()

    for part in re.split(r"[,;\n]", raw_skills or ""):
        name = part.strip()
        if not name:
            continue
        key = name.lower()
        name = canonical.get(key) or ALIASES.get(key) or name
        if name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)

    for skill in extract_skills(description or ""):
        if skill.lower() not in seen:
            seen.add(skill.lower())
            result.append(skill)

    return result