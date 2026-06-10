from utils.profile import load_profile
PROFILE = load_profile()
"""
resume_builder.py — ATS-optimised single-page PDF resume via ReportLab.
"""

import asyncio
import logging
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    HRFlowable, KeepTogether, Table, TableStyle
)
from reportlab.lib import colors

logger = logging.getLogger(__name__)
# Base directory for all job-specific output folders
JOBS_BASE_DIR = Path("output/jobs")
JOBS_BASE_DIR.mkdir(parents=True, exist_ok=True)
# Legacy flat dirs kept so existing callers don't crash (not used for new runs)
OUTPUT_DIR = Path("output/resumes")
CL_DIR = Path("output/cover_letters")

BLACK = colors.black
WHITE = colors.white
GRAY = colors.HexColor("#444444")


def mk_styles(fs=8.5):
    lh = fs * 1.28
    return dict(
        name=ParagraphStyle("name",
            fontName="Helvetica-Bold", fontSize=fs+8,
            leading=(fs+8)*1.2, alignment=TA_CENTER,
            textColor=BLACK, spaceAfter=1),

        contact=ParagraphStyle("contact",
            fontName="Helvetica", fontSize=fs-1,
            leading=(fs-1)*1.3, alignment=TA_CENTER,
            textColor=GRAY, spaceAfter=6),

        sec=ParagraphStyle("sec",
            fontName="Helvetica-Bold", fontSize=fs,
            leading=lh, textColor=BLACK,
            spaceBefore=7, spaceAfter=1),

        etitle=ParagraphStyle("etitle",
            fontName="Helvetica-Bold", fontSize=fs,
            leading=lh, textColor=BLACK, spaceAfter=0),

        edate=ParagraphStyle("edate",
            fontName="Helvetica", fontSize=fs-1,
            leading=lh, textColor=BLACK,
            alignment=TA_RIGHT),

        bullet=ParagraphStyle("bullet",
            fontName="Helvetica", fontSize=fs,
            leading=lh, textColor=BLACK,
            leftIndent=12, firstLineIndent=-8,
            alignment=TA_JUSTIFY, spaceAfter=1.5),

        body=ParagraphStyle("body",
            fontName="Helvetica", fontSize=fs,
            leading=lh, textColor=BLACK,
            alignment=TA_JUSTIFY, spaceAfter=1),

        skill=ParagraphStyle("skill",
            fontName="Helvetica", fontSize=fs,
            leading=lh, textColor=BLACK,
            spaceAfter=1.2),
    )


def hr():
    return HRFlowable(width="100%", thickness=0.45,
                      color=colors.HexColor("#888888"),
                      spaceAfter=3, spaceBefore=0)


# ── Parser ────────────────────────────────────────────────────────────────────
ALIASES = {
    "education":                     "education",
    "technical skills":              "skills",
    "skills":                        "skills",
    "experience":                    "experience",
    "projects":                      "projects",
    "projects & research":           "projects",
    "certifications":                "certs",
    "certifications & achievements": "certs",
    "achievements":                  "certs",
    "languages & volunteer":         "extra",
    "languages":                     "extra",
    "volunteer":                     "extra",
    "summary":                       "summary",
    "objective":                     "summary",
}
DISPLAY = {
    "education":  "EDUCATION",
    "skills":     "TECHNICAL SKILLS",
    "experience": "EXPERIENCE",
    "projects":   "PROJECTS & RESEARCH",
    "certs":      "CERTIFICATIONS & ACHIEVEMENTS",
    "extra":      "LANGUAGES & VOLUNTEER",
    "summary":    "SUMMARY",
}
ORDER = ["education","skills","experience","projects","certs","extra","summary"]

_SEC_RE = re.compile(
    r"^(" + "|".join(re.escape(k) for k in ALIASES) + r")\s*$",
    re.IGNORECASE)
_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|20\d\d|Present)\b")


def parse(text: str) -> dict:
    buckets = {k: [] for k in set(ALIASES.values())}
    buckets["_contact"] = []
    cur = "_contact"
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _SEC_RE.match(line.strip())
        if m:
            cur = ALIASES[m.group(1).lower()]
        else:
            buckets[cur].append(line.strip())
    return buckets


# ── Title + Date splitter ─────────────────────────────────────────────────────
def split_title_date(line: str):
    """
    Splits 'Some Title — Org Name   Aug – Dec 2023' into (title, date).
    Strategy: find the LAST date-like token group, everything before = title.
    """
    # Match date range at the very end, preceded by 2+ spaces
    m = re.search(
        r"\s{2,}"
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)?[\s\w]*"
        r"(?:20\d\d|Present)"
        r"(?:\s*[–\-]\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[\s\w]*)?(?:20\d\d|Present)?)?)"
        r"\s*$",
        line
    )
    if m:
        title = line[:m.start()].rstrip(" —–|-\t").strip()
        dt    = m.group(1).strip()
        # Strip verbose "(Expected ...)" — too long for date col
        dt = re.sub(r"\s*\([^)]*\)", "", dt).strip()
        return title, dt

    # Fallback: split on 2+ spaces or tab
    parts = re.split(r"\s{2,}|\t", line, maxsplit=1)
    if len(parts) == 2:
        dt = re.sub(r"\s*\([^)]*\)", "", parts[1]).strip()
        return parts[0].strip(), dt
    return line.strip(), ""


# ── Title+date row: date always on ONE line, never wraps ─────────────────────
def title_date_row(title: str, dt: str, S, usable_w: float):
    """
    Key fix: measure the date string width and give it exactly that much
    space + a small buffer. Title gets the rest. This guarantees the date
    column is never too narrow to fit on one line.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    date_fs   = S["edate"].fontSize
    date_font = S["edate"].fontName
    dt_width  = stringWidth(dt, date_font, date_fs) + 6   # +6pt buffer

    # Cap: date col max 30% of page, min enough to fit the string
    max_date_w = usable_w * 0.30
    date_col_w = min(max(dt_width, 60), max_date_w)
    title_col_w = usable_w - date_col_w

    left  = Paragraph(title, S["etitle"])
    right = Paragraph(dt,    S["edate"])

    tbl = Table([[left, right]],
                colWidths=[title_col_w, date_col_w],
                hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN",         (0,0), (-1,-1), "TOP"),
        ("ALIGN",          (1,0), (1,0),   "RIGHT"),
        ("LEFTPADDING",    (0,0), (-1,-1), 0),
        ("RIGHTPADDING",   (0,0), (-1,-1), 0),
        ("TOPPADDING",     (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 2),
    ]))
    return tbl


# ── Section builders ──────────────────────────────────────────────────────────
def build_skills(lines, S):
    out = [Paragraph("TECHNICAL SKILLS", S["sec"]), hr()]
    for line in lines:
        if not line:
            continue
        if ":" in line:
            lbl, _, rest = line.partition(":")
            out.append(Paragraph(
                f"<b>{lbl.strip()}:</b> {rest.strip()}", S["skill"]))
        else:
            out.append(Paragraph(line, S["body"]))
    return out


def build_section(key, lines, S, usable_w):
    out = [Paragraph(DISPLAY[key], S["sec"]), hr()]
    prev_was_bullet = False

    for i, line in enumerate(lines):
        if not line:
            continue
        is_bullet = line.startswith(("•", "-", "·", "*"))
        has_date  = bool(_DATE_RE.search(line)) and not is_bullet

        if has_date:
            if prev_was_bullet:
                out.append(Spacer(1, 3))
            title, dt = split_title_date(line)
            out.append(title_date_row(title, dt, S, usable_w))
            prev_was_bullet = False
        elif is_bullet:
            text = line.lstrip("•-·* ").strip()
            out.append(Paragraph(f"• {text}", S["bullet"]))
            prev_was_bullet = True
        else:
            out.append(Paragraph(line, S["body"]))
            prev_was_bullet = False

    return out


def _md_to_rl(text: str) -> str:
    """Convert **bold** markdown to ReportLab <b> tags."""
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)


def build_cover_letter_pdf(cl_text: str, output_path: Path, title: str = "Cover Letter", job: dict = None) -> Path:
    """Render a cover letter as clean paragraphs with header."""
    from datetime import date as _date
    fs = 10.5
    lh = fs * 1.45

    name_style = ParagraphStyle("cl_name",
        fontName="Helvetica-Bold", fontSize=12,
        leading=14, alignment=TA_CENTER,
        textColor=BLACK, spaceAfter=3)
    contact_style = ParagraphStyle("cl_contact",
        fontName="Helvetica", fontSize=9,
        leading=11, alignment=TA_CENTER,
        textColor=GRAY, spaceAfter=12)
    body_style = ParagraphStyle("cl_body",
        fontName="Helvetica", fontSize=fs, leading=lh,
        textColor=BLACK, spaceAfter=10, alignment=TA_JUSTIFY)
    meta_style = ParagraphStyle("cl_meta",
        fontName="Helvetica", fontSize=fs, leading=lh,
        textColor=BLACK, spaceAfter=4)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        leftMargin=0.85*inch, rightMargin=0.85*inch,
        topMargin=0.7*inch, bottomMargin=0.7*inch,
        title=title,
    )

    company = job.get("company", "Hiring Team") if job else "Hiring Team"
    today = _date.today().strftime("%B %d, %Y")

    story = [
        Paragraph(PROFILE.get("full_name","Candidate"), name_style),
        Paragraph(
            f"{PROFILE.get('email','you@example.com')}  |  {PROFILE.get('phone_display', PROFILE.get('phone',''))}  |  {PROFILE.get('linkedin','')}  |  {PROFILE.get('location','')}",
            contact_style,
        ),
        HRFlowable(width="100%", thickness=0.45, color=colors.HexColor("#888888"),
                   spaceAfter=12, spaceBefore=0),
        Paragraph(today, meta_style),
        Spacer(1, 6),
        Paragraph(f"Hiring Team", meta_style),
        Paragraph(company, meta_style),
        Spacer(1, 12),
        Paragraph("Dear Hiring Team,", meta_style),
        Spacer(1, 6),
    ]

    for chunk in re.split(r'\n{2,}', cl_text.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        chunk = chunk.replace("&", "&amp;").replace("<", "&lt;")
        chunk = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', chunk)
        chunk = chunk.replace("\n", " ")
        story.append(Paragraph(chunk, body_style))

    story += [
        Spacer(1, 16),
        Paragraph("Sincerely,", meta_style),
        Spacer(1, 24),
        Paragraph(f"<b>{PROFILE.get('full_name','Candidate')}</b>", meta_style),
    ]

    doc.build(story)
    return output_path


# ── Render + auto-fit ─────────────────────────────────────────────────────────
def _render(text, path, title, fs, lr, tm, bm):
    S  = mk_styles(fs)
    parsed = parse(text)

    doc = SimpleDocTemplate(
        str(path), pagesize=letter,
        leftMargin=lr, rightMargin=lr,
        topMargin=tm, bottomMargin=bm,
        title=title, author=PROFILE.get("full_name","Candidate"),
    )
    usable_w = letter[0] - 2 * lr
    story = []

    contact = parsed.get("_contact", [])
    if contact:
        story.append(Paragraph(contact[0], S["name"]))
        rest = [l.strip() for l in contact[1:] if l.strip()]
        if rest:
            story.append(Paragraph("  |  ".join(rest), S["contact"]))
    story.append(Spacer(1, 2))

    for key in ORDER:
        lines = parsed.get(key, [])
        if not lines:
            continue
        block = build_skills(lines, S) if key == "skills" \
                else build_section(key, lines, S, usable_w)
        story.append(KeepTogether(block[:5]))
        story.extend(block[5:])
        story.append(Spacer(1, 1))

    doc.build(story)


def build_resume_pdf(resume_text: str, output_path: Path, title: str = "Resume") -> Path:
    configs = [
        (8.5,  0.60*inch, 0.50*inch, 0.42*inch),
        (8.2,  0.55*inch, 0.44*inch, 0.36*inch),
        (7.9,  0.52*inch, 0.39*inch, 0.32*inch),
        (7.6,  0.48*inch, 0.34*inch, 0.28*inch),
        (7.3,  0.44*inch, 0.30*inch, 0.24*inch),
    ]
    tmp = Path(tempfile.mktemp(suffix=".pdf"))

    for fs, lr, tm, bm in configs:
        try:
            _render(resume_text, tmp, title, fs, lr, tm, bm)
        except Exception as e:
            logger.warning(f"[ResumeBuilder] render err fs={fs}: {e}")
            continue
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(str(tmp)).pages)
        except Exception:
            try:
                from PyPDF2 import PdfReader
                pages = len(PdfReader(str(tmp)).pages)
            except Exception:
                pages = 1  # assume it fits if we can't check
        if pages == 1:
            tmp.replace(output_path)
            logger.info(f"[ResumeBuilder] 1-page fs={fs:.1f} → {output_path.name}")
            return output_path

    _render(resume_text, output_path, title, *configs[-1])
    if tmp.exists(): tmp.unlink()
    logger.warning(f"[ResumeBuilder] Could not fit to 1 page → {output_path.name}")
    return output_path


# ── Async wrapper + helpers ──────────────────────────────────────────────────
class ResumeBuilder:
    def __init__(self, grok_client):
        self.grok = grok_client
        self.jobs_base_dir = JOBS_BASE_DIR

    # ── Per-job folder helpers ────────────────────────────────────────────────

    def _slug_for_job(self, job: dict) -> str:
        """Create a deterministic slug for a job (company + title)."""
        company = (job.get("company") or "").strip()
        title   = (job.get("title")   or "").strip()
        slug = re.sub(r"[^\w]+", "_", f"{company}_{title}")[:60]
        return slug or "job"

    def job_folder(self, job: dict) -> Path:
        """
        Return (and create) the output folder for this job:
            output/jobs/<YYYYMMDD>_<company>_<title>/
        """
        today = date.today().strftime("%Y%m%d")
        slug  = self._slug_for_job(job)
        folder = self.jobs_base_dir / f"{today}_{slug}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _resume_filename(self, job: dict, base_resume_name: str) -> str:
        slug = self._slug_for_job(job)
        return f"{base_resume_name}_{slug}.pdf"

    def _cover_filename(self, job: dict, base_resume_name: str) -> str:
        slug = self._slug_for_job(job)
        return f"CoverLetter_{slug}.pdf"

    def get_expected_resume_path(self, job: dict, base_resume) -> Path:
        folder   = self.job_folder(job)
        filename = self._resume_filename(job, getattr(base_resume, "name", "candidate_resume"))
        return folder / filename

    def get_expected_cover_path(self, job: dict, base_resume) -> Path:
        folder   = self.job_folder(job)
        filename = self._cover_filename(job, getattr(base_resume, "name", "candidate_resume"))
        return folder / filename

    def find_existing_resume(self, job: dict, base_resume) -> Optional[Path]:
        p = self.get_expected_resume_path(job, base_resume)
        if p.exists():
            return p
        # Also search the per-job folder for any resume PDF
        folder = self.job_folder(job)
        for f in folder.glob("*.pdf"):
            if "CoverLetter" not in f.name:
                return f
        return None

    def find_existing_cover_letter(self, job: dict, base_resume) -> Optional[Path]:
        p = self.get_expected_cover_path(job, base_resume)
        if p.exists():
            return p
        folder = self.job_folder(job)
        for f in folder.glob("CoverLetter_*.pdf"):
            return f
        return None

    # ── Build methods ─────────────────────────────────────────────────────────

    async def build(self, job, base_resume) -> Path:
        """
        Tailor the resume using the LLM client, then render to PDF in the
        job-specific output folder.
        """
        job_safe = dict(job or {})
        job_safe["description"] = job_safe.get("description") or job_safe.get("teaser") or ""
        job_safe["teaser"]      = job_safe.get("teaser")      or job_safe.get("description") or ""

        out_path = self.get_expected_resume_path(job_safe, base_resume)

        if out_path.exists():
            logger.info(f"[ResumeBuilder] Found existing tailored resume: {out_path}")
            return out_path

        try:
            tailored_text = await self.grok.tailor_resume(base_resume.text, job_safe)
        except Exception as e:
            logger.error(
                f"[ResumeBuilder] Tailoring failed for {job.get('title')} at {job.get('company')}: {e}",
                exc_info=True,
            )
            raise

        tailored_text = re.sub(r'\*\*(.+?)\*\*', r'\1', tailored_text)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, build_resume_pdf, tailored_text, out_path,
            f"{job.get('title','')} — {job.get('company','')}"
        )
        logger.info(f"[ResumeBuilder] Wrote tailored resume → {out_path}")
        return out_path

    async def build_cover_letter(self, job, base_resume) -> Path:
        """
        Generate a cover letter PDF in the job-specific output folder.
        """
        job_safe = dict(job or {})
        job_safe["description"] = job_safe.get("description") or job_safe.get("teaser") or ""
        job_safe["teaser"]      = job_safe.get("teaser")      or job_safe.get("description") or ""

        out_path = self.get_expected_cover_path(job_safe, base_resume)
        if out_path.exists():
            logger.info(f"[ResumeBuilder] Found existing cover letter: {out_path}")
            return out_path

        try:
            if hasattr(self.grok, "generate_cover_letter"):
                cl_text = await self.grok.generate_cover_letter(
                    job_safe, base_resume.text, PROFILE.get("full_name", "Candidate")
                )
            else:
                prompt = (
                    f"Write a concise cover letter for the role {job_safe.get('title')} at {job_safe.get('company')}.\n\n"
                    f"Job description: {job_safe.get('description')[:1000]}\n\n"
                    f"Candidate summary: {base_resume.text[:2000]}"
                )
                if hasattr(self.grok, "_call_model"):
                    cl_text = await self.grok._call_model(prompt, max_tokens=800)
                else:
                    cl_text = f"Dear Hiring Team,\n\nPlease find my application attached.\n\nSincerely,\n{PROFILE.get('full_name','Candidate')}"
        except Exception as e:
            logger.warning(f"[ResumeBuilder] Cover letter generation failed: {e}", exc_info=True)
            cl_text = f"Dear Hiring Team,\n\nPlease find my application attached.\n\nSincerely,\n{PROFILE.get('full_name','Candidate')}"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, build_cover_letter_pdf, cl_text, out_path,
            f"Cover Letter — {job.get('title','')}", job_safe
        )
        logger.info(f"[ResumeBuilder] Wrote cover letter → {out_path}")
        return out_path

