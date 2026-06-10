# pipeline.py
"""
Job Hunter pipeline — v2.

What changed from v1:
  - No auto-apply.  The pipeline now PREPARES everything (resume, cover letter,
    email draft) and saves a per-run manifest (output/runs/<run_id>/manifest.json).
  - Email drafts are saved to Gmail as drafts (not sent) via the Gmail API.
    If Gmail API is unavailable, the draft is written as a .eml file instead.
  - LinkedIn session manager removed; no browser login required.
  - Per-job output folders: output/jobs/<date>_<company>_<title>/
  - Two dashboards:
      python dashboard_run.py   — latest run results + apply links
      python dashboard.py       — cumulative application history tracker
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from scrapers.apify_linkedin import ApifyScraper
from scrapers.apify_handshake import ApifyHandshakeScraper
from ai.grok_client import GrokClient
from resume.resume_selector import ResumeSelector
from resume.resume_builder import ResumeBuilder
from application.email_draft import EmailDraftSaver
from application.tracker import ApplicationTracker

log = logging.getLogger(__name__)

RUNS_DIR = Path("output/runs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)


class JobHunterPipeline:
    def __init__(self, args):
        self.args = args
        self.config = json.loads(Path(args.config).read_text())
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.tracker       = ApplicationTracker()
        self.grok          = GrokClient()
        self.resume_selector = ResumeSelector(args.resumes_dir, self.grok)
        self.resume_builder  = ResumeBuilder(self.grok)
        self.email_drafter   = EmailDraftSaver(self.config)

        boards = getattr(args, "boards", ["linkedin", "handshake"])

        # LinkedIn via Apify
        self.apify_scraper = None
        if "linkedin" in boards:
            token = os.getenv("APIFY_API_TOKEN") or os.getenv("APIFY_TOKEN")
            if token:
                self.apify_scraper = ApifyScraper(token)
            else:
                log.warning("⚠️ APIFY_API_TOKEN not set — LinkedIn scraping disabled")

        # Handshake via Apify actor
        self.handshake_scraper = None
        if "handshake" in boards:
            apify_token = os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_TOKEN")
            apify_actor = os.getenv("APIFY_ACTOR_ID") or os.getenv("APIFY_ACTOR")
            if not apify_token or not apify_actor:
                raise EnvironmentError(
                    "APIFY_TOKEN (or APIFY_API_TOKEN) and APIFY_ACTOR_ID must be set for Handshake scraping"
                )
            self.handshake_scraper = ApifyHandshakeScraper(
                token=apify_token, actor_id=apify_actor, timeout=600
            )
            log.info("Using Apify actor for Handshake scraping: %s", apify_actor)

    # ──────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY
    # ──────────────────────────────────────────────────────────────────────────

    async def run(self):
        log.info("🚀 Job Hunter v2 starting — run id: %s", self.run_id)

        all_jobs = await self._scrape_all()
        if not all_jobs:
            log.warning("⚠️ No jobs collected from any source.")
            return

        new_jobs = [
            j for j in all_jobs
            if not self.tracker.already_applied(j.get("id") or j.get("url"))
        ]
        if not new_jobs:
            log.info("😴 All collected jobs have already been processed.")
            return

        log.info("📋 %d new positions to process (cap: %d)", len(new_jobs), self.args.max_jobs)

        min_score = getattr(self.args, "min_score", 0.6)
        scored    = await self._score_jobs(new_jobs)
        eligible  = [job for job, score in scored if score >= min_score]

        log.info(
            "🎯 %d jobs above score threshold %.1f (filtered %d)",
            len(eligible), min_score, len(new_jobs) - len(eligible),
        )
        if not eligible:
            log.info("😶 No jobs met the minimum score threshold.")
            return

        manifest_entries = []
        for job in eligible[: self.args.max_jobs]:
            entry = await self._prepare_job(job)
            if entry:
                manifest_entries.append(entry)

        self._save_manifest(manifest_entries)
        log.info(
            "✅ Run complete — %d jobs prepared.  Open the run dashboard to review.",
            len(manifest_entries),
        )
        log.info("   python dashboard_run.py --run %s", self.run_id)

    # ──────────────────────────────────────────────────────────────────────────
    # SCRAPING
    # ──────────────────────────────────────────────────────────────────────────

    async def _scrape_all(self) -> list[dict]:
        if getattr(self.args, "mock", False):
            log.warning("🧪 MOCK MODE — no real scraping.")
            return [
                {
                    "id": "mock-1",
                    "title": "Software Engineer Intern",
                    "company": "MockTech",
                    "url": "https://mock/job/1",
                    "apply_url": "https://mock/apply/1",
                    "easy_apply_url": "https://www.linkedin.com/jobs/view/123456/",
                    "external_url": None,
                    "description": "We are looking for a Python intern...",
                    "source": "mock",
                },
                {
                    "id": "mock-2",
                    "title": "Data Science Intern",
                    "company": "MockData",
                    "url": "https://mock/job/2",
                    "apply_url": None,
                    "easy_apply_url": None,
                    "external_url": "https://boards.greenhouse.io/mockdata/jobs/999",
                    "description": "Machine learning, pandas, numpy...",
                    "source": "mock",
                },
            ]

        all_jobs: list[dict] = []

        if self.apify_scraper:
            log.info("📡 Scraping LinkedIn via Apify...")
            try:
                search_url = self.config.get(
                    "SEARCH_URL",
                    "https://www.linkedin.com/jobs/search/?keywords=Software%20Engineer%20Intern",
                )
                max_items = self.config.get("linkedin_max_items", 100)
                jobs = self.apify_scraper.scrape_linkedin(search_url, max_items=max_items)
                for j in jobs:
                    j.setdefault("source", "linkedin")
                log.info("✅ LinkedIn: %d jobs", len(jobs))
                all_jobs.extend(jobs)
            except Exception as e:
                log.error("❌ LinkedIn scraper failed: %s", e, exc_info=True)

        if self.handshake_scraper:
            log.info("📡 Scraping Handshake via Apify actor...")
            try:
                keywords  = self.config.get("handshake_keywords", ["software engineer intern"])
                max_items = int(os.environ.get("APIFY_MAX_ITEMS", str(self.config.get("handshake_max_items", 100))))
                use_proxy = os.environ.get("APIFY_USE_PROXY", "true").lower() in ("1", "true", "yes")
                jobs = await self.handshake_scraper.scrape(keywords=keywords, max_items=max_items, use_proxy=use_proxy)
                for j in jobs:
                    j.setdefault("source", "handshake")
                log.info("✅ Handshake (Apify): %d jobs", len(jobs))
                all_jobs.extend(jobs)
            except Exception as e:
                log.error("❌ Handshake (Apify) scraper failed: %s", e, exc_info=True)

        # Dedup
        seen, unique = set(), []
        for job in all_jobs:
            jid = job.get("id") or job.get("url") or job.get("apply_url")
            if not jid:
                unique.append(job)
            elif jid not in seen:
                seen.add(jid)
                unique.append(job)

        log.info("📦 Total unique jobs collected: %d", len(unique))
        return unique

    # ──────────────────────────────────────────────────────────────────────────
    # SCORING
    # ──────────────────────────────────────────────────────────────────────────

    async def _score_jobs(self, jobs: list[dict]) -> list[tuple[dict, float]]:
        log.info("🔍 Scoring %d jobs...", len(jobs))
        resume_summaries = await self._get_resume_summaries()
        batch_size = 10
        scored = []
        for i in range(0, len(jobs), batch_size):
            batch = jobs[i : i + batch_size]
            try:
                scores = await self.grok.batch_score_jobs(batch, resume_summaries)
                scored.extend(zip(batch, scores))
            except Exception as e:
                log.warning("[Score] Batch failed: %s — defaulting to 0.5", e)
                scored.extend((job, 0.5) for job in batch)
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    async def _get_resume_summaries(self) -> str:
        try:
            selector = self.resume_selector
            if not getattr(selector, "_resumes", None):
                selector._load_resumes()
            return "\n\n".join(
                f"[{r.name}]: {r.text[:400]}" for r in (selector._resumes or [])
            )
        except Exception:
            return "M.S. Computer Science student. Python, ML, full-stack."

    # ──────────────────────────────────────────────────────────────────────────
    # PER-JOB PREPARATION  (no auto-apply)
    # ──────────────────────────────────────────────────────────────────────────

    async def _prepare_job(self, job: dict) -> dict | None:
        title   = job.get("title",   "Unknown")
        company = job.get("company", "Unknown")
        source  = job.get("source",  "?")
        log.info("🛠 [%s] %s at %s", source.upper(), title, company)

        try:
            base_resume = await self.resume_selector.select(job)

            if self.args.dry_run:
                log.info("👀 [Dry Run] Skipping file generation for: %s at %s", title, company)
                return None

            # Build resume + cover letter into per-job folder
            tailored_pdf = await self.resume_builder.build(job, base_resume)
            cover_pdf    = await self.resume_builder.build_cover_letter(job, base_resume)
            job_folder   = self.resume_builder.job_folder(job)

            # Save email draft (to Gmail drafts or .eml file)
            draft_link = await self.email_drafter.save_draft(job, tailored_pdf, cover_pdf, job_folder)

            # Build manifest entry
            entry = {
                "run_id":        self.run_id,
                "id":            job.get("id"),
                "title":         title,
                "company":       company,
                "source":        source,
                "job_url":       job.get("url") or job.get("apply_url"),
                "easy_apply_url": job.get("easy_apply_url"),
                "external_url":  job.get("external_url"),
                "handshake_url": job.get("url") if source == "handshake" else None,
                "email_draft_link": draft_link,
                "resume_path":   str(tailored_pdf.relative_to(Path("."))),
                "cover_letter_path": str(cover_pdf.relative_to(Path("."))),
                "job_folder":    str(job_folder.relative_to(Path("."))),
                "score":         job.get("_score"),
                "prepared_at":   datetime.now().isoformat(),
                "recruiter_email": job.get("recruiter_email"),
                "company_li_url": job.get("company_li_url"),
                "contacts":      job.get("contacts", []),  # LinkedIn alumni/colleagues
                "status":        "pending",  # user sets to applied / rejected / etc.
            }

            # Write a per-job JSON summary too
            (job_folder / "job_info.json").write_text(
                json.dumps({k: v for k, v in entry.items() if k != "run_id"}, indent=2),
                encoding="utf-8",
            )

            log.info("📁 Job folder ready: %s", job_folder)
            return entry

        except Exception as e:
            log.error("💥 Failed to prepare %s at %s: %s", title, company, e, exc_info=True)
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # MANIFEST
    # ──────────────────────────────────────────────────────────────────────────

    def _save_manifest(self, entries: list[dict]):
        run_dir = RUNS_DIR / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        log.info("📝 Manifest saved → %s", manifest_path)
