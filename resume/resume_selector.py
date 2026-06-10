"""
Resume selector — reads all your resume PDFs, extracts text,
and uses Grok to pick the best one for each job.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

log = logging.getLogger(__name__)


@dataclass
class Resume:
    name: str           # filename stem e.g. "resume_backend"
    path: Path          # full path to PDF
    text: str           # extracted plain text
    summary: str        # short AI-generated summary (lazy loaded)


class ResumeSelector:
    def __init__(self, resumes_dir: str, grok):
        self.resumes_dir = Path(resumes_dir)
        self.grok = grok
        self._resumes: list[Resume] | None = None

    def _load_resumes(self) -> list[Resume]:
        if self._resumes is not None:
            return self._resumes

        pdfs = sorted(self.resumes_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No resume PDFs found in {self.resumes_dir}")

        resumes = []
        for pdf_path in pdfs:
            text = self._extract_text(pdf_path)
            resumes.append(Resume(
                name=pdf_path.stem,
                path=pdf_path,
                text=text,
                summary=self._quick_summary(text),
            ))
            log.info(f"[Resumes] Loaded: {pdf_path.name} ({len(text)} chars)")

        self._resumes = resumes
        return resumes

    def get_all_summaries(self) -> str:
        """Return a combined text summary of all resumes for batch scoring."""
        resumes = self._load_resumes()
        parts = []
        for r in resumes:
            parts.append(f"=== {r.name} ===\n{r.summary}")
        return "\n\n".join(parts)

    async def select(self, job: dict) -> Resume:
        """Pick the best resume for the given job."""
        resumes = self._load_resumes()

        if len(resumes) == 1:
            return resumes[0]

        options = [{"name": r.name, "summary": r.summary} for r in resumes]
        best_name = await self.grok.select_best_resume(job, options)

        for r in resumes:
            if r.name == best_name:
                return r

        log.warning(f"[Resumes] Grok returned unknown resume '{best_name}', using first")
        return resumes[0]

    def get_by_name(self, name: str) -> Resume | None:
        for r in self._load_resumes():
            if r.name == name:
                return r
        return None

    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(pdf_path: Path) -> str:
        """Extract plain text from a PDF using pdfplumber."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n".join(pages)
        except Exception as e:
            log.error(f"[Resumes] Failed to read {pdf_path.name}: {e}")
            return ""

    @staticmethod
    def _quick_summary(text: str) -> str:
        """Extract a short summary from resume text (no AI needed — just first 400 chars)."""
        # Trim whitespace and take the first meaningful chunk
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return "\n".join(lines[:15])[:400]
