#!/usr/bin/env python3
"""
SponsorScan Personalized Report — EXPANDED NEW-GRAD + OPT-AWARE FILTER

Features
--------
V2: Experience/education-aware filtering and scoring
V3: Resume-weighted skill matching
V4: Company-fit ranking
V5: Application priority and explanation columns
V6: Daily "new since last run" report and history tracking
V7: Keeps only jobs posted within the requested number of hours
V8: Uses database job IDs, broader CS role families, and fit-first ranking
V9: Remembers reported jobs for 60 days and scores SpeedyApply feed rows

Place this file beside sponsorscan.db, then run after fetch-jobs:

    python sponsorscan.py fetch-jobs --replace
    python sponsor_daily_report.py --out pranav_matches.csv --new-out todays_new_jobs.csv --top 50

First run:
    All matching jobs are treated as new because no prior snapshot exists.

Later runs:
    todays_new_jobs.csv contains only jobs not matched in the last 60 days.
"""

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sponsorscan import FEED_SOURCES

DB_PATH = Path("sponsorscan.db")
DEFAULT_STATE = Path(".sponsorscan_pranav_state.json")

# ---------------------------- Role fit ---------------------------------

# The fourth value marks role families whose titles are commonly used outside
# software. Those roles are kept only when the posting also contains multiple
# concrete software/data signals.
TARGET_ROLES = [
    ("Machine Learning Engineer",
     [r"\bmachine learning engineer\b", r"\bml engineer\b",
      r"\bml platform engineer\b"], 64, False),
    ("AI/ML Engineer",
     [r"\bai[ /-]?ml engineer\b", r"\bai engineer\b",
      r"\bartificial intelligence engineer\b",
      r"\bgenerative ai engineer\b", r"\bgenai engineer\b"], 64, False),
    ("Research Engineer",
     [r"\bresearch engineer\b",
      r"\bmachine learning research engineer\b",
      r"\bai research engineer\b"], 62, False),
    ("Applied Scientist",
     [r"\bapplied scientist\b",
      r"\bapplied machine learning scientist\b"], 61, False),
    ("Computer Vision Engineer",
     [r"\bcomputer vision engineer\b", r"\bvision engineer\b"], 60, False),
    ("ML Researcher",
     [r"\bml researcher\b", r"\bmachine learning researcher\b",
      r"\bai researcher\b"], 59, False),
    ("Research Scientist",
     [r"\bresearch scientist\b", r"\bai scientist\b"], 58, False),
    ("Data Scientist",
     [r"\bdata scientist\b", r"\bmachine learning scientist\b"], 57, False),
    ("Data Engineer",
     [r"\bdata engineer\b", r"\banalytics engineer\b"], 52, False),
    ("Backend Engineer",
     [r"\bback[ -]?end (?:software )?engineer\b",
      r"\bbackend developer\b"], 50, False),
    ("Full Stack Engineer",
     [r"\bfull[ -]?stack (?:software )?engineer\b",
      r"\bfull[ -]?stack developer\b"], 50, False),
    ("Platform Engineer",
     [r"\bplatform (?:software )?engineer\b",
      r"\bplatform developer\b"], 50, False),
    ("Systems Software Engineer",
     [r"\bsystems? software engineer\b",
      r"\bsoftware systems? engineer\b",
      r"\bsystems? developer\b"], 49, False),
    ("Cloud/Infrastructure Engineer",
     [r"\bcloud (?:software )?engineer\b",
      r"\binfrastructure (?:software )?engineer\b",
      r"\bcloud developer\b"], 49, False),
    ("DevOps/SRE",
     [r"\bdevops engineer\b", r"\bsite reliability engineer\b",
      r"\bsre\b"], 49, False),
    ("Embedded Software Engineer",
     [r"\bembedded software engineer\b", r"\bfirmware engineer\b",
      r"\bembedded developer\b"], 49, False),
    ("Python Engineer",
     [r"\bpython engineer\b", r"\bpython developer\b"], 49, False),
    ("Mobile Engineer",
     [r"\bmobile (?:software )?engineer\b",
      r"\bios (?:software )?engineer\b",
      r"\bandroid (?:software )?engineer\b",
      r"\bmobile developer\b"], 48, False),
    ("Application Developer",
     [r"\bapplications? (?:software )?(?:engineer|developer)\b",
      r"\bapplication programmer\b"], 48, True),
    ("QA Automation Engineer",
     [r"\bqa automation engineer\b",
      r"\btest automation engineer\b",
      r"\bsoftware quality engineer\b",
      r"\bsoftware test engineer\b",
      r"\bquality assurance engineer\b"], 47, False),
    ("Security Engineer",
     [r"\bapplication security engineer\b",
      r"\bsoftware security engineer\b",
      r"\bcybersecurity engineer\b",
      r"\bsecurity engineer\b"], 47, True),
    ("Integration Engineer",
     [r"\bintegration (?:software )?(?:engineer|developer)\b"], 46, True),
    ("BI Engineer",
     [r"\bbusiness intelligence (?:engineer|developer)\b",
      r"\bbi (?:engineer|developer)\b",
      r"\banalytics developer\b"], 46, False),
    ("Web Developer",
     [r"\bweb (?:application )?developer\b",
      r"\bweb software engineer\b"], 46, False),
    ("Data Analyst",
     [r"\bdata analyst\b", r"\banalytics analyst\b"], 45, True),
    ("Cybersecurity Analyst",
     [r"\bcybersecurity analyst\b",
      r"\binformation security analyst\b",
      r"\bsecurity analyst\b"], 45, True),
    ("Technology Analyst",
     [r"\btechnology analyst\b", r"\bit analyst\b",
      r"\bprogrammer analyst\b", r"\bsoftware analyst\b",
      r"\bsystems analyst\b"], 44, True),
    ("Technology Associate",
     [r"\bdigital technology associate\b",
      r"\btechnology associate\b",
      r"\btechnology rotational (?:program|associate)\b",
      r"\bit rotational (?:program|associate)\b"], 46, True),
    ("Product Engineer",
     [r"\bproduct (?:software )?engineer\b"], 46, True),
    ("Software Engineer",
     [r"\bsoftware engineer\b", r"\bsoftware developer\b",
      r"\bsoftware development engineer\b", r"\bsde\b"], 47, False),
]
TARGET_ROLES = [
    (name, [re.compile(p, re.I) for p in pats], score, broad)
    for name, pats, score, broad in TARGET_ROLES
]

TECH_ROLE_SIGNALS = [
    ("Python", re.compile(r"\bpython\b", re.I)),
    ("Java", re.compile(r"\bjava\b", re.I)),
    ("C/C++", re.compile(r"\bc(?:\+\+|#)?\b", re.I)),
    ("JavaScript/TypeScript",
     re.compile(r"\b(?:javascript|typescript|node\.?js|react)\b", re.I)),
    ("SQL", re.compile(r"\bsql\b|\bpostgres(?:ql)?\b|\bmysql\b", re.I)),
    ("Software development",
     re.compile(r"\bsoftware (?:development|engineering|design)\b", re.I)),
    ("Programming", re.compile(r"\bprogramming\b|\bdevelop(?:ing|ment)? code\b", re.I)),
    ("APIs/services",
     re.compile(r"\bapi(?:s)?\b|\bmicroservices?\b|\bbackend\b|\bfrontend\b", re.I)),
    ("Cloud",
     re.compile(r"\baws\b|\bazure\b|\bgcp\b|\bcloud\b", re.I)),
    ("DevOps",
     re.compile(r"\bdocker\b|\bkubernetes\b|\bci/?cd\b|\bgithub actions\b", re.I)),
    ("Data",
     re.compile(r"\bdata pipeline\b|\betl\b|\banalytics\b|\bdata warehouse\b", re.I)),
    ("Machine learning",
     re.compile(r"\bmachine learning\b|\bdeep learning\b|\bllm", re.I)),
    ("Computer science",
     re.compile(r"\bcomputer science\b|\bcomputer engineering\b", re.I)),
    ("Automation", re.compile(r"\bautomation\b|\bautomated testing\b", re.I)),
]


def technical_role_signals(description):
    text = description or ""
    return [label for label, pattern in TECH_ROLE_SIGNALS if pattern.search(text)]


# ---------------------------- Resume fit -------------------------------

# Higher weights reflect the user's strongest demonstrated experience.
RESUME_SKILLS = [
    ("Python", [r"\bpython\b"], 7),
    ("Machine Learning", [r"\bmachine learning\b", r"\bml\b"], 7),
    ("Deep Learning", [r"\bdeep learning\b", r"\bneural network"], 6),
    ("TensorFlow/Keras", [r"\btensorflow\b", r"\bkeras\b"], 6),
    ("PyTorch", [r"\bpytorch\b"], 6),
    ("Research/Publications", [r"\bresearch\b", r"\bpublication\b", r"\bieee\b",
                                r"\bscientific\b"], 6),
    ("NLP/LLMs", [r"\bnlp\b", r"\bllm", r"\blarge language model"], 5),
    ("RAG/LangChain", [r"\brag\b", r"\bretrieval.?augmented\b", r"\blangchain\b"], 5),
    ("Time Series/Forecasting", [r"\btime.?series\b", r"\bforecast"], 5),
    ("SQL", [r"\bsql\b", r"\bmysql\b", r"\bpostgres(?:ql)?\b", r"\bsqlite\b"], 4),
    ("Scikit-learn", [r"\bscikit.?learn\b", r"\bsklearn\b"], 4),
    ("Pandas/NumPy", [r"\bpandas\b", r"\bnumpy\b"], 4),
    ("Computer Vision", [r"\bcomputer vision\b", r"\bimage processing\b"], 4),
    ("APIs/Cloud", [r"\bapi\b", r"\baws\b", r"\bazure\b", r"\bgcp\b",
                     r"\bcloud\b"], 3),
    ("React/Node", [r"\breact\b", r"\bnode\.?js\b"], 3),
    ("Flask/Streamlit", [r"\bflask\b", r"\bstreamlit\b"], 3),
    ("C++", [r"\bc\+\+\b"], 2),
    ("Java", [r"\bjava\b"], 2),
    ("JavaScript", [r"\bjavascript\b", r"\btypescript\b"], 2),
    ("Git/GitHub", [r"\bgit\b", r"\bgithub\b"], 2),
    ("DevOps/CI/CD", [r"\bdocker\b", r"\bkubernetes\b",
                       r"\bci/?cd\b", r"\bgithub actions\b"], 3),
]
RESUME_SKILLS = [
    (name, [re.compile(p, re.I) for p in pats], score)
    for name, pats, score in RESUME_SKILLS
]

# -------------------------- Hard eligibility ---------------------------

# Phase 1: reject jobs that are clearly outside the user's eligibility.
# Only jobs that survive this phase are allowed to reach scoring.

POSITIVE_LEVEL_PATTERNS = [
    ("new grad", re.compile(r"\bnew ?grad(?:uate)?\b", re.I), 18),
    ("university graduate", re.compile(r"\b(?:university|college) grad(?:uate)?\b", re.I), 16),
    ("early career", re.compile(r"\bearly career\b", re.I), 14),
    ("entry level", re.compile(r"\bentry[ -]?level\b", re.I), 14),
    ("0–2 years", re.compile(r"\b0\s*(?:-|–|—|to)\s*2\s*(?:years?|yrs?)\b", re.I), 14),
    ("0–1 years", re.compile(r"\b0\s*(?:-|–|—|to)\s*1\s*(?:years?|yrs?)\b", re.I), 14),
    ("1–2 years", re.compile(r"\b1\s*(?:-|–|—|to)\s*2\s*(?:years?|yrs?)\b", re.I), 11),
    ("associate", re.compile(r"\bassociate\b", re.I), 7),
    ("level I", re.compile(r"\b(?:engineer|scientist|developer)\s+i\b", re.I), 8),
    ("internship", re.compile(r"\bintern(?:ship)?\b", re.I), 8),
    ("bachelor's", re.compile(r"\bbachelor'?s?(?: degree)?\b", re.I), 5),
]

SENIOR_TITLE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|distinguished|lead|architect|manager|"
    r"director|head of|vp|vice president|chief|fellow)\b",
    re.I,
)

PHD_TITLE = re.compile(
    r"\b(?:ph\.?\s*d\.?|doctoral|doctorate|post[ -]?doc(?:toral)?)\b",
    re.I,
)

LEVEL_II_PLUS_TITLE = re.compile(
    r"(?:"
    r"\b(?:engineer|scientist|developer|analyst|researcher)\s*(?:,|-)?\s*(?:ii|iii|iv|v|2|3|4|5)\b"
    r"|\b(?:level|lvl)\s*(?:2|3|4|5|ii|iii|iv|v)\b"
    r"|\b(?:swe|sde|mle)\s*(?:2|3|4|5|ii|iii|iv|v)\b"
    r")",
    re.I,
)

# Intentionally strict: reject any Master's or PhD mention in the description.
# This follows the user's latest preference to avoid advanced-degree roles.
ADVANCED_DEGREE_RE = re.compile(
    r"\b(?:ph\.?\s*d\.?|doctorate|doctoral(?: degree)?|"
    r"master'?s(?: degree)?|m\.?\s*s\.?|m\.?\s*sc\.?|graduate degree)\b",
    re.I,
)

POSTDOC_FACULTY_RE = re.compile(
    r"\b(?:postdoctoral|post-doc|postdoc|faculty|professor)\b",
    re.I,
)

# Experience requirements appear in many forms. The numeric expression is
# detected first, then its surrounding sentence is checked for qualification or
# technical context. This avoids rejecting a job because it mentions "5 years
# of service", a company anniversary, or a benefits vesting period.
EXPERIENCE_REQUIREMENT_RE = re.compile(
    r"\b(?P<min>\d{1,2})\s*"
    r"(?:\+|(?:-|–|—|to)\s*(?P<max>\d{1,2}))?\s*"
    r"(?:years?|yrs?)\b",
    re.I,
)

EXPERIENCE_CONTEXT_RE = re.compile(
    r"\b(?:experience|professional|industry|engineering|software|development|"
    r"developer|programming|building|built|working|hands[ -]?on|background|"
    r"expertise|proficiency|knowledge|minimum|at least|required|requirements?|"
    r"qualifications?|must have|python|java|c\+\+|javascript|typescript|sql|"
    r"backend|frontend|full[ -]?stack|cloud|data|machine learning|automation)\b",
    re.I,
)

NON_EXPERIENCE_CONTEXT_RE = re.compile(
    r"\b(?:years? of service|service anniversary|vesting|benefits?|founded|"
    r"company history|serving customers|age \d|older than)\b",
    re.I,
)


def extract_experience_requirements(description):
    text = description or ""
    requirements = []

    for match in EXPERIENCE_REQUIREMENT_RE.finditer(text):
        left = max(
            text.rfind(".", 0, match.start()),
            text.rfind("\n", 0, match.start()),
            text.rfind(";", 0, match.start()),
        )
        right_candidates = [
            position for position in (
                text.find(".", match.end()),
                text.find("\n", match.end()),
                text.find(";", match.end()),
            )
            if position != -1
        ]
        right = min(right_candidates) if right_candidates else len(text)
        context = text[max(left + 1, match.start() - 100):min(right, match.end() + 180)]

        if NON_EXPERIENCE_CONTEXT_RE.search(context) and not re.search(
            r"\b(?:experience|required|minimum|at least|must have)\b",
            context,
            re.I,
        ):
            continue

        if not EXPERIENCE_CONTEXT_RE.search(context):
            continue

        requirements.append(
            (
                int(match.group("min")),
                int(match.group("max")) if match.group("max") else None,
                re.sub(r"\s+", " ", context).strip(),
            )
        )

    return requirements


# -------------------------- Sponsorship fit ----------------------------

# OPT-aware hard exclusions.
#
# A generic statement such as "no visa sponsorship available" is NOT enough by
# itself to remove a job, because an OPT/STEM OPT candidate may already have
# temporary employment authorization. We only reject language that clearly
# blocks OPT candidates, requires permanent/unrestricted authorization, limits
# the role to citizens/permanent residents, or requires clearance/export status.
DISQUALIFIERS = [
    # Explicit OPT/CPT exclusion.
    r"\b(?:opt|stem opt|cpt)\b[^.\n]{0,100}"
    r"\b(?:not accepted|not eligible|not supported|will not be considered|cannot be hired)\b",
    r"\b(?:not accepting|cannot employ|unable to employ)\b[^.\n]{0,100}"
    r"\b(?:opt|stem opt|cpt)\b",

    # Requires permanent or unrestricted authorization.
    r"\b(?:permanent|unrestricted)\s+(?:u\.?\s?s\.?\s+)?work authorization\b",
    r"\bmust be\b[^.\n]{0,100}\b(?:permanent resident|green card holder)\b",
    r"\b(?:must|need to)\b[^.\n]{0,100}\b(?:not require|never require)\b"
    r"[^.\n]{0,100}\b(?:current or future|now or future)\b[^.\n]{0,60}\bsponsorship\b",

    # Citizenship, clearance, and regulated-access restrictions.
    r"\bmust be (?:a |an )?(?:u\.?\s?s\.?|united states)\s?(?:citizen|person|national)\b",
    r"\b(?:u\.?\s?s\.?|united states)\s?citizenship (?:is )?required\b",
    r"\b(?:u\.?\s?s\.?|united states)\s?(?:citizens?|persons?)\s+only\b",
    r"\bsecurity clearance\b",
    r"\bitar\b",
    r"\bexport control(?:led|s)?\b",
]
SPONSOR_POSITIVE = [
    r"\bvisa sponsorship (?:is )?available\b",
    r"\bwe (?:will )?sponsor\b",
    r"\bh-?1b sponsorship\b",
    r"\bsponsorship (?:is )?(?:available|offered|provided)\b",
]
DISQ_RE = [re.compile(p, re.I) for p in DISQUALIFIERS]
SPONSOR_POS_RE = [re.compile(p, re.I) for p in SPONSOR_POSITIVE]

# ---------------------------- Company fit ------------------------------

COMPANY_TIERS = {
    5: {
        "openai", "anthropic", "waymo", "databricks", "snowflake", "nvidia",
        "scale ai", "figma", "roblox"
    },
    4: {
        "pinterest", "reddit", "airbnb", "twilio", "robinhood", "samsara",
        "zoox", "palantir", "stripe", "coreweave", "datadog", "mongodb",
        "jane street", "moloco"
    },
    3: {
        "spotify", "toast", "roku", "gen digital", "nuro", "doordash",
        "benchling", "netflix"
    },
}
COMPANY_TIER_POINTS = {5: 8, 4: 6, 3: 4}

# ---------------------------- US location ------------------------------

US_STATE_CODES = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in",
    "ia","ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv",
    "nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn",
    "tx","ut","vt","va","wa","wv","wi","wy","dc",
}
US_STATE_NAMES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut",
    "delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa",
    "kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan",
    "minnesota","mississippi","missouri","montana","nebraska","nevada",
    "new hampshire","new jersey","new mexico","new york","north carolina",
    "north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island",
    "south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming",
    "district of columbia",
}
NON_US = {
    "canada","india","united kingdom","ireland","germany","france","spain","italy",
    "netherlands","belgium","sweden","norway","denmark","finland","poland",
    "romania","portugal","switzerland","austria","australia","new zealand",
    "singapore","japan","china","taiwan","south korea","korea","israel","brazil",
    "mexico","argentina","colombia","chile","philippines","indonesia","malaysia",
    "thailand","vietnam","hong kong","uae","dubai","berlin","toronto","vancouver",
    "london","dublin","paris","amsterdam","munich",
}
NON_US_RE = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, NON_US))) + r")\b")


def normalize_company(name):
    value = (name or "").lower()
    value = re.sub(r"\b(incorporated|corporation|company|limited|llc|inc|corp|ltd)\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url):
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        # Legacy state keys removed query strings; retained only for one-time migration.
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    except Exception:
        return url.strip()


def stable_job_key(company, title, location, url):
    normalized_url = normalize_url(url)
    if normalized_url:
        return normalized_url
    text = "|".join([
        normalize_company(company),
        re.sub(r"\s+", " ", (title or "").lower()).strip(),
        re.sub(r"\s+", " ", (location or "").lower()).strip(),
    ])
    return text


def classify_role(title, description):
    role_signals = technical_role_signals(description)

    for family, patterns, base, requires_tech_signals in TARGET_ROLES:
        if not any(pattern.search(title or "") for pattern in patterns):
            continue

        if requires_tech_signals and len(role_signals) < 2:
            continue

        return family, base, role_signals

    return None, 0, role_signals


def score_skills(title, description):
    text = f"{title or ''}\n{description or ''}"
    matched, points = [], 0
    for name, patterns, value in RESUME_SKILLS:
        if any(p.search(text) for p in patterns):
            matched.append(name)
            points += value
    return matched, min(points, 42)


def career_level_score(title, description):
    title_text = title or ""
    description_text = description or ""
    text = f"{title_text}\n{description_text}"

    # ---------------- Phase 1: hard eligibility ----------------

    if PHD_TITLE.search(title_text):
        return None, "PhD/doctoral/postdoc title", [], None

    if LEVEL_II_PLUS_TITLE.search(title_text):
        return None, "level II or above title", [], None

    if SENIOR_TITLE.search(title_text):
        return None, "senior-level title", [], None

    if POSTDOC_FACULTY_RE.search(text):
        return None, "postdoctoral/faculty role", [], None

    if ADVANCED_DEGREE_RE.search(description_text):
        matched = ADVANCED_DEGREE_RE.search(description_text).group(0)
        if re.search(r"ph\.?\s*d\.?|doctorate|doctoral", matched, re.I):
            return None, "PhD mentioned in description", [], None
        return None, "Master's mentioned in description", [], None

    requirements = extract_experience_requirements(description_text)

    # Use every detected qualification. Therefore a posting that mentions
    # "1 year with Python" and "5+ years overall" is still rejected.
    disqualifying = [minimum for minimum, _, _ in requirements if minimum > 1]
    if disqualifying:
        required = max(disqualifying)
        return None, f"{required}+ years required", [], required

    years_required = max(
        (minimum for minimum, _, _ in requirements),
        default=None,
    )

    # ---------------- Phase 2: entry-level scoring ----------------

    signals = []
    bonus = 0

    for label, pattern, points in POSITIVE_LEVEL_PATTERNS:
        if pattern.search(text):
            signals.append(label)
            bonus = max(bonus, points)

    if years_required == 0:
        bonus += 10
        signals.append("0 years required")
    elif years_required == 1:
        bonus += 6
        signals.append("1 year requirement")

    return bonus, None, signals, years_required


def company_tier(company):
    normalized = normalize_company(company)
    for tier, names in COMPANY_TIERS.items():
        if any(name in normalized or normalized in name for name in names):
            return tier
    return 2


def is_us_location(location):
    loc = (location or "").strip().lower()
    if not loc:
        return False
    # Whole words, so "india" does not match "Indiana" or "Indianapolis".
    if NON_US_RE.search(loc):
        return False
    if any(term in loc for term in (
        "united states", "usa", "u.s.", "remote - us", "remote, us",
        "remote us", "us remote", "remote (us)", "north america"
    )):
        return True
    if any(state in loc for state in US_STATE_NAMES):
        return True
    tokens = set(re.findall(r"\b[a-z]{2}\b", loc))
    return bool(tokens & US_STATE_CODES)


def load_employers(con):
    try:
        rows = con.execute(
            "SELECT employer_norm, employer_display, certified, denied, withdrawn, "
            "titles, states, lvl1, lvl2, lvl3, lvl4 FROM employers"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Could not read employers table: {exc}")

    output = {}
    for row in rows:
        levels = [row[7] or 0, row[8] or 0, row[9] or 0, row[10] or 0]
        level_total = sum(levels)
        case_total = (row[2] or 0) + (row[3] or 0) + (row[4] or 0)
        try:
            titles = json.loads(row[5] or "[]")
        except json.JSONDecodeError:
            titles = []

        output[row[0]] = {
            "display": row[1],
            "certified": row[2] or 0,
            "titles": [str(x).lower() for x in titles],
            "senior_share": ((levels[2] + levels[3]) / level_total) if level_total else None,
            "trouble_rate": (((row[3] or 0) + (row[4] or 0)) / case_total) if case_total else None,
        }
    return output


def sponsorship_score(emp, title, blob):
    """
    Score sponsorship history as supporting evidence, not as the main fit score.
    The value is capped so a famous high-volume filer cannot turn a weak resume
    match into an Apply recommendation.
    """
    score = 0
    signals = []

    if emp and emp["certified"] > 0:
        score += 8
        signals.append(f"{emp['certified']} certified LCAs")

        if emp["certified"] >= 100:
            score += 5
        elif emp["certified"] >= 25:
            score += 3
        elif emp["certified"] >= 5:
            score += 1

        words = [
            word for word in re.findall(r"[a-z]+", (title or "").lower())
            if len(word) >= 5
        ]
        if words and any(
            any(word in historical_title for word in words)
            for historical_title in emp["titles"]
        ):
            score += 4
            signals.append("historical LCA title overlap")

        if emp["trouble_rate"] is not None and emp["trouble_rate"] >= 0.25:
            score -= 3
            signals.append("higher denied/withdrawn share")

    if any(pattern.search(blob) for pattern in SPONSOR_POS_RE):
        score += 5
        signals.append("posting mentions sponsorship")

    return max(-3, min(score, 22)), signals


def priority_label(score, resume_fit, career_score, matched_skill_count):
    """
    Application priority is gated by personal fit. Company prestige and LCA
    history can improve the score but cannot create a high priority by themselves.
    """
    if (
        score >= 115
        and resume_fit >= 78
        and matched_skill_count >= 4
        and career_score >= 0
    ):
        return "P1 — Apply ASAP"

    if score >= 100 and resume_fit >= 68 and matched_skill_count >= 2:
        return "P2 — Strong Apply"

    if (
        score >= 85
        and (
            (resume_fit >= 58 and matched_skill_count >= 1)
            or (career_score >= 11 and resume_fit >= 50)
        )
    ):
        return "P3 — Apply"

    return "P4 — Review"


# The state remembers every match it has reported, not only the ones present
# this run. A job missing for a run (its board failed to fetch, or it briefly
# fell out of the filters) is still remembered when it comes back, so it is not
# emailed twice. Keys unseen for this long are forgotten to bound the file.
SEEN_RETENTION_DAYS = 60


def load_previous_state(path):
    """Return a dict mapping job key to the ISO date it was last matched."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if "seen" in data:
        return dict(data["seen"])
    # Older state files listed only the keys matched on the last run.
    today = datetime.now(timezone.utc).date().isoformat()
    return {key: today for key in data.get("active_job_keys", [])}


def update_seen(seen, current_keys, today):
    """Stamp this run's keys with today and drop those unseen past retention."""
    cutoff = (today - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    updated = {key: day for key, day in seen.items() if day >= cutoff}
    updated.update({key: today.isoformat() for key in current_keys})
    return updated


def save_state(path, seen):
    payload = {
        "version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seen": dict(sorted(seen.items())),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)



def parse_posted_datetime(value):
    """Return an aware UTC datetime, or None when the ATS supplied no usable date."""
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    # Unix timestamps in seconds or milliseconds.
    if re.fullmatch(r"\d{10,13}", raw):
        stamp = int(raw)
        if len(raw) == 13:
            stamp /= 1000
        try:
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    normalized = raw.replace("Z", "+00:00")

    # Common ISO-like formats. Every ATS fetcher stores a bare date, which
    # fromisoformat would read as midnight, so leave that to the date-only
    # handling below.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                # A date-only ATS value has no exact posting time. Use the end of
                # that UTC day so a newly posted job is not prematurely discarded.
                parsed = parsed.replace(hour=23, minute=59, second=59)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def is_recent_post(posted, cutoff):
    parsed = parse_posted_datetime(posted)
    return parsed is not None and parsed >= cutoff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="pranav_matches_48h.csv",
                        help="All currently matching jobs")
    parser.add_argument("--new-out", default="todays_new_jobs_48h.csv",
                        help="Only jobs new since the previous run")
    parser.add_argument("--state", default=str(DEFAULT_STATE),
                        help="Snapshot file used to identify new jobs")
    parser.add_argument("--top", type=int, default=50,
                        help="How many top jobs to print")
    parser.add_argument("--min-score", type=int, default=70,
                        help="Minimum combined score to keep")
    parser.add_argument("--include-non-us", action="store_true")
    parser.add_argument("--include-internships", action="store_true",
                        help="Internships are included by default only if title matches target roles")
    parser.add_argument("--reset-state", action="store_true",
                        help="Treat all current matches as new")
    parser.add_argument("--hours", type=float, default=168,
                        help="Keep only jobs posted within this many hours (default: 168)")
    parser.add_argument("--include-unknown-posted", action="store_true",
                        help="Also keep jobs whose ATS provides no usable posting date")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit("Could not find sponsorscan.db in this folder.")

    state_path = Path(args.state)
    previous_keys = {} if args.reset_state else load_previous_state(state_path)

    if args.hours <= 0:
        raise SystemExit("--hours must be greater than 0.")
    now_utc = datetime.now(timezone.utc)
    posted_cutoff = now_utc - timedelta(hours=args.hours)

    con = sqlite3.connect(DB_PATH)
    employers = load_employers(con)

    try:
        jobs = con.execute(
            "SELECT job_key, company, company_norm, title, location, url, posted, description, source "
            "FROM jobs"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Could not read jobs table: {exc}")

    results = []
    counts = {
        "disqualifier": 0, "role": 0, "career": 0,
        "phd_title": 0, "level_title": 0, "experience_2plus": 0,
        "phd_required": 0, "masters_required": 0,
        "location": 0, "score": 0, "weak_fit": 0,
        "posted_too_old": 0, "posted_unknown": 0,
    }

    for database_job_key, company, company_norm, title, location, url, posted, description, source in jobs:
        parsed_posted = parse_posted_datetime(posted)
        if parsed_posted is None:
            if not args.include_unknown_posted:
                counts["posted_unknown"] += 1
                continue
        elif parsed_posted < posted_cutoff:
            counts["posted_too_old"] += 1
            continue

        blob = f"{title or ''}\n{description or ''}"

        if any(p.search(blob) for p in DISQ_RE):
            counts["disqualifier"] += 1
            continue

        role_family, role_base, role_tech_signals = classify_role(title, description)
        if not role_family:
            counts["role"] += 1
            continue

        level_score, exclusion_reason, level_signals, years_required = career_level_score(
            title, description
        )
        if level_score is None:
            counts["career"] += 1
            if exclusion_reason == "PhD/doctoral/postdoc title":
                counts["phd_title"] += 1
            elif exclusion_reason == "level II or above title":
                counts["level_title"] += 1
            elif exclusion_reason and exclusion_reason.endswith("+ years required"):
                counts["experience_2plus"] += 1
            elif exclusion_reason == "PhD mentioned in description":
                counts["phd_required"] += 1
            elif exclusion_reason == "Master's mentioned in description":
                counts["masters_required"] += 1
            continue

        # Feed rows come from a curated new grad list but carry no description,
        # so the title alone rarely shows an entry-level signal, and the
        # experience, degree and work-authorization checks above saw nothing.
        is_feed = source in FEED_SOURCES
        if is_feed:
            level_signals = level_signals + ["on SpeedyApply new grad list"]
            level_score = max(level_score, 14)

        if not args.include_non_us and not is_us_location(location):
            counts["location"] += 1
            continue

        skills, skill_score = score_skills(title, description)
        resume_fit = min(100, role_base + skill_score)

        # Do not let a company/LCA score rescue a role that has neither a
        # demonstrated resume-skill match nor an explicit entry-level signal.
        if not skills and not level_signals:
            counts["weak_fit"] += 1
            continue

        tier = company_tier(company)
        company_points = COMPANY_TIER_POINTS.get(tier, 2)

        emp = employers.get(company_norm)
        sponsor_points, sponsor_signals = sponsorship_score(emp, title, blob)

        combined_score = (
            resume_fit
            + level_score
            + company_points
            + sponsor_points
        )

        if combined_score < args.min_score:
            counts["score"] += 1
            continue

        # Use the provider-specific key already stored by sponsorscan.py.
        # The legacy URL key is checked once so existing state files migrate
        # without emailing every currently active job again.
        legacy_key = stable_job_key(company, title, location, url)
        key = database_job_key or legacy_key
        is_new = key not in previous_keys and legacy_key not in previous_keys

        reasons = [
            f"{resume_fit}/100 resume fit",
            role_family,
            f"company tier {tier}/5",
        ]
        reasons.extend(level_signals)
        if role_tech_signals:
            reasons.append("technical signals: " + ", ".join(role_tech_signals[:5]))
        reasons.extend(sponsor_signals)
        if is_feed:
            reasons.append("title only: no description to check requirements")

        results.append({
            "application_priority": priority_label(
                combined_score, resume_fit, level_score, len(skills)
            ),
            "combined_score": combined_score,
            "resume_fit_score": resume_fit,
            "career_level_score": level_score,
            "company_tier": tier,
            "company_fit_points": company_points,
            "sponsorship_score": sponsor_points,
            "is_new_since_last_run": "YES" if is_new else "NO",
            "role_family": role_family,
            "matched_resume_skills": ", ".join(skills),
            "experience_years_detected": years_required if years_required is not None else "",
            "company": company,
            "title": title,
            "location": location,
            "posted": posted,
            "source": source,
            "why_ranked": "; ".join(reasons),
            "url": url,
            "lca_certified": emp["certified"] if emp else 0,
            "job_key": key,
        })

    priority_rank = {
        "P1 — Apply ASAP": 1,
        "P2 — Strong Apply": 2,
        "P3 — Apply": 3,
        "P4 — Review": 4,
    }
    results.sort(
        key=lambda row: (
            priority_rank.get(row["application_priority"], 9),
            -row["combined_score"],
            -row["resume_fit_score"],
            -(parse_posted_datetime(row["posted"]).timestamp()
              if parse_posted_datetime(row["posted"]) else 0),
            row["company"].lower(),
        )
    )

    new_results = [row for row in results if row["is_new_since_last_run"] == "YES"]

    fields = [
        "application_priority", "combined_score", "resume_fit_score",
        "career_level_score", "company_tier", "company_fit_points",
        "sponsorship_score", "is_new_since_last_run", "role_family",
        "matched_resume_skills", "experience_years_detected", "company",
        "title", "location", "posted", "source", "why_ranked", "url",
        "lca_certified", "job_key",
    ]

    write_csv(Path(args.out), results, fields)
    write_csv(Path(args.new_out), new_results, fields)
    save_state(state_path,
               update_seen(previous_keys, {row["job_key"] for row in results},
                           now_utc.date()))

    print(f"Wrote {len(results):,} jobs posted within the last {args.hours:g} hours to {args.out}")
    print(f"Wrote {len(new_results):,} jobs new since the previous run to {args.new_out}")
    print(f"Posting cutoff (UTC): {posted_cutoff.isoformat()}")
    if not previous_keys:
        print("No previous snapshot was found, so all current matches were counted as new.")
    print()
    print(f"{counts['posted_too_old']:,} dropped because they were older than {args.hours:g} hours")
    print(f"{counts['posted_unknown']:,} dropped because no usable posting time was supplied")
    print(f"{counts['disqualifier']:,} dropped for hard work-authorization/citizenship/clearance restrictions")
    print(f"{counts['career']:,} dropped for career-level restrictions")
    print(f"  - {counts['phd_title']:,} had PhD/doctoral/postdoc in the title")
    print(f"  - {counts['level_title']:,} had Level II/III/IV/V titles")
    print(f"  - {counts['experience_2plus']:,} required more than 1 year of experience")
    print(f"  - {counts['phd_required']:,} mentioned a PhD in the description")
    print(f"  - {counts['masters_required']:,} mentioned a master's degree in the description")
    print(f"{counts['role']:,} dropped outside your target role list")
    print(f"{counts['location']:,} dropped as non-US or unrecognized")
    print(f"{counts['weak_fit']:,} dropped because they had no resume-skill or entry-level signal")
    print(f"{counts['score']:,} dropped below combined score {args.min_score}")
    print()

    display_rows = new_results if new_results else results
    heading = "TOP NEW JOBS" if new_results else "TOP CURRENT JOBS"
    print(heading)
    print("=" * len(heading))

    for row in display_rows[:args.top]:
        print(
            f"[{row['combined_score']:>3}] {row['application_priority']} | "
            f"{row['company']} — {row['title']}"
        )
        print(
            f"      {row['location'] or '?'} | {row['role_family']} | "
            f"Resume {row['resume_fit_score']}/100 | Company {row['company_tier']}/5"
        )
        print(f"      Skills: {row['matched_resume_skills'] or 'title match only'}")
        print(f"      {row['url']}\n")


if __name__ == "__main__":
    main()
