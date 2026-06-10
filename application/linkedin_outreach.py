from utils.profile import load_profile
PROFILE = load_profile()
"""
application/linkedin_outreach.py

Post-application outreach — after applying to a job, finds a relevant hiring
contact at the company on LinkedIn and sends a connection request with a
personalised note.

Flow:
  1. Search LinkedIn for hiring managers / recruiters at the company
  2. Check for common ground (shared school, shared city, shared field)
  3. Send a connection request with a casual 300-char note
  4. If Connect is blocked → find email → send via Gmail SMTP

Uses the persistent LinkedIn session saved by:
    python main.py --setup-session linkedin

Rate limiting: LinkedIn allows ~15-20 connection requests/day safely.
The pipeline enforces this via a daily counter stored in logs/outreach_counts.json
"""

import asyncio
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

RATE_LIMIT_FILE   = Path("logs/outreach_counts.json")
DAILY_LIMIT       = 15    # safe LinkedIn daily connection request limit

BACKGROUND = {
    "schools": ["arizona state", "asu", "vellore institute", "vit"],
    "city":    ["tempe", "phoenix", "scottsdale", "chandler", "mesa"],
    "field":   ["computer science", "software", "machine learning", "data science", "ml", "ai"],
}

TARGET_TITLES = [
    "hiring manager",
    "engineering manager",
    "recruiter",
    "talent acquisition",
    "technical recruiter",
    "university recruiter",
    "campus recruiter",
    "head of engineering",
    "vp of engineering",
    "director of engineering",
    "software engineering lead",
    "team lead",
    "hr",
]


class LinkedInOutreach:
    def __init__(self):
        self._context     = None   # Playwright BrowserContext — shared across jobs
        self._shared_page = None
        self._grok        = None

    def _get_grok(self):
        if self._grok is None:
            from ai.grok_client import GrokClient
            self._grok = GrokClient()
        return self._grok

    # ── Entry point ───────────────────────────────────────────────────────────

    async def reach_out(self, job: dict) -> bool:
        """
        Find a hiring contact for this job and reach out.
        Returns True if any outreach was sent.
        """
        if not self._check_rate_limit():
            log.info("[Outreach] Daily LinkedIn limit reached — skipping outreach for today")
            return False

        # Lazily open the saved LinkedIn session
        if self._shared_page is None:
            page = await self._get_page()
            if page is None:
                return False
            self._shared_page = page

        page = self._shared_page
        try:
            contacts = await self._search_contacts(page, job)
            if not contacts:
                log.info(f"[Outreach] No contacts found for {job.get('company')}")
                return False

            contacts = self._rank_contacts(contacts, job)
            log.info(
                f"[Outreach] Found {len(contacts)} contact(s) — "
                f"top: {contacts[0].get('name')} ({contacts[0].get('title')})"
            )

            for contact in contacts[:3]:
                sent = await self._outreach_contact(page, job, contact)
                if sent:
                    self._increment_rate_limit()
                    return True

            return False

        except Exception as e:
            log.error(f"[Outreach] Error: {e}", exc_info=True)
            # Reset on error so next call gets a fresh page
            try:
                await self._shared_page.close()
            except Exception:
                pass
            self._shared_page = None
            return False

    # ── Session management ────────────────────────────────────────────────────

    async def _get_page(self):
        """Return a page from the saved LinkedIn session. Returns None if not set up."""
        try:
            from utils.session_manager import SessionManager, SessionNotFoundError
            self._context = await SessionManager.get_context("linkedin")
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            return page
        except Exception as e:
            log.error(
                f"[Outreach] Could not open LinkedIn session: {e}\n"
                f"  Run: python main.py --setup-session linkedin"
            )
            return None

    async def close(self):
        """Clean up browser resources. Call this when the pipeline finishes."""
        if self._shared_page:
            try:
                await self._shared_page.close()
            except Exception:
                pass
            self._shared_page = None

        if self._context:
            from utils.session_manager import SessionManager
            await SessionManager.close_context(self._context)
            self._context = None

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        """Return True if we're under the daily limit."""
        counts = self._load_counts()
        today  = str(date.today())
        return counts.get(today, 0) < DAILY_LIMIT

    def _increment_rate_limit(self):
        counts = self._load_counts()
        today  = str(date.today())
        counts[today] = counts.get(today, 0) + 1
        RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_FILE.write_text(json.dumps(counts, indent=2))
        log.info(f"[Outreach] Daily count: {counts[today]}/{DAILY_LIMIT}")

    def _load_counts(self) -> dict:
        if RATE_LIMIT_FILE.exists():
            try:
                return json.loads(RATE_LIMIT_FILE.read_text())
            except Exception:
                pass
        return {}

    # ── LinkedIn people search ────────────────────────────────────────────────

    async def _search_contacts(self, page, job: dict) -> list[dict]:
        """Search LinkedIn for hiring managers / recruiters at the company."""
        company  = job.get("company", "")
        contacts = []

        for title_query in ["recruiter", "hiring manager", "engineering manager"]:
            query = f"{title_query} {company}"
            url   = (
                f"https://www.linkedin.com/search/results/people/"
                f"?keywords={query.replace(' ', '%20')}"
                f"&origin=GLOBAL_SEARCH_HEADER"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)

            # Try multiple selector strategies — LinkedIn DOM changes often
            cards = page.locator("li.reusable-search__result-container")
            if await cards.count() == 0:
                cards = page.locator(".entity-result")
            if await cards.count() == 0:
                cards = page.locator("ul.reusable-search__entity-result-list > li")

            count = await cards.count()
            log.debug(f"[Outreach] '{title_query} {company}' → {count} result cards")

            for i in range(min(count, 5)):
                card = cards.nth(i)
                try:
                    name = await self._extract_text(card, [
                        ".entity-result__title-text a span[aria-hidden='true']",
                        ".entity-result__title-text a",
                        ".app-aware-link span[aria-hidden='true']",
                    ])
                    if not name or name == "LinkedIn Member":
                        continue

                    title = await self._extract_text(card, [
                        ".entity-result__primary-subtitle",
                        ".entity-result__secondary-subtitle",
                        ".subline-level-1",
                    ])

                    href = await self._extract_attr(card, [
                        "a.app-aware-link[href*='/in/']",
                        ".entity-result__title-text a",
                    ], "href")

                    if href and "/in/" in href:
                        profile_url = href.split("?")[0]
                        contacts.append({
                            "name":        name,
                            "title":       title,
                            "profile_url": profile_url,
                            "common":      [],
                        })
                except Exception as e:
                    log.debug(f"[Outreach] Card parse error: {e}")

            await asyncio.sleep(1.5)

        # Deduplicate by profile URL
        seen, unique = set(), []
        for c in contacts:
            if c["profile_url"] not in seen:
                seen.add(c["profile_url"])
                unique.append(c)

        return unique

    async def _extract_text(self, element, selectors: list[str]) -> str:
        for sel in selectors:
            el = element.locator(sel)
            if await el.count() > 0:
                text = (await el.first.inner_text()).strip()
                if text:
                    return text
        return ""

    async def _extract_attr(self, element, selectors: list[str], attr: str) -> str:
        for sel in selectors:
            el = element.locator(sel)
            if await el.count() > 0:
                val = await el.first.get_attribute(attr) or ""
                if val:
                    return val
        return ""

    # ── Profile enrichment ───────────────────────────────────────────────────

    async def _enrich_contact(self, page, contact: dict) -> dict:
        """Visit the profile and look for common ground with PROFILE.get('first_name','Candidate')."""
        try:
            await page.goto(contact["profile_url"], wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2)

            profile_text = (await page.locator("main").inner_text()).lower()
            common = []

            for school in BACKGROUND["schools"]:
                if school in profile_text:
                    common.append(f"both went to {school.upper() if school == 'asu' else school.title()}")
                    break

            for city in BACKGROUND["city"]:
                if city in profile_text:
                    common.append(f"both in the {city.title()} area")
                    break

            shared_el = page.locator("span.dist-value, .member-insights__count")
            if await shared_el.count() > 0:
                shared_text = await shared_el.first.inner_text()
                if any(c.isdigit() for c in shared_text):
                    common.append(f"{shared_text.strip()} shared connection(s)")

            contact["common"] = common
        except Exception as e:
            log.debug(f"[Outreach] Profile enrich error: {e}")

        return contact

    # ── Contact ranking ───────────────────────────────────────────────────────

    def _rank_contacts(self, contacts: list[dict], job: dict) -> list[dict]:
        def score(c):
            title_lower = c.get("title", "").lower()
            title_score = 0
            for i, t in enumerate(TARGET_TITLES):
                if t in title_lower:
                    title_score = len(TARGET_TITLES) - i
                    break
            return title_score + len(c.get("common", [])) * 3
        return sorted(contacts, key=score, reverse=True)

    # ── Single contact outreach ───────────────────────────────────────────────

    async def _outreach_contact(self, page, job: dict, contact: dict) -> bool:
        contact = await self._enrich_contact(page, contact)
        note    = await self._generate_note(job, contact)
        log.info(f"[Outreach] Messaging {contact['name']}: {note[:80]}…")

        sent = await self._send_linkedin_connection(page, contact, note)
        if sent:
            log.info(f"[Outreach] ✅ LinkedIn connection sent to {contact['name']}")
            return True

        # Fallback to email
        log.info(f"[Outreach] LinkedIn blocked — trying email for {contact['name']}")
        email = await self._find_email(page, contact, job)
        if email:
            sent = await self._send_email_outreach(job, contact, note, email)
            if sent:
                log.info(f"[Outreach] ✅ Email sent to {email}")
                return True

        log.warning(f"[Outreach] Could not reach {contact['name']} via any channel")
        return False

    # ── LinkedIn connection request ───────────────────────────────────────────

    async def _send_linkedin_connection(self, page, contact: dict, note: str) -> bool:
        try:
            await page.goto(contact["profile_url"], wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2)

            connect_btn = page.locator('button:has-text("Connect")')

            if await connect_btn.count() == 0:
                more_btn = page.locator('button:has-text("More")')
                if await more_btn.count() > 0:
                    await more_btn.first.click()
                    await asyncio.sleep(1)
                    connect_btn = page.locator(
                        'div[role="option"]:has-text("Connect"), li:has-text("Connect")'
                    )

            if await connect_btn.count() == 0:
                log.debug(f"[Outreach] No Connect button for {contact['name']}")
                return False

            await connect_btn.first.click()
            await asyncio.sleep(1.5)

            add_note_btn = page.locator('button:has-text("Add a note")')
            if await add_note_btn.count() > 0:
                await add_note_btn.click()
                await asyncio.sleep(1)

            # Check for premium InMail wall
            premium_wall = page.locator('button:has-text("Send InMail"), h2:has-text("InMail")')
            if await premium_wall.count() > 0:
                log.debug("[Outreach] InMail wall — skipping")
                close = page.locator('button[aria-label="Dismiss"], button:has-text("Cancel")')
                if await close.count() > 0:
                    await close.first.click()
                return False

            note_area = page.locator('textarea[name="message"], textarea#custom-message')
            if await note_area.count() > 0:
                await note_area.first.fill(note[:300])
            else:
                log.debug("[Outreach] No note textarea — sending without note")

            send_btn = page.locator('button:has-text("Send"), button:has-text("Send invitation")')
            if await send_btn.count() > 0 and await send_btn.first.is_visible():
                await send_btn.first.click()
                await asyncio.sleep(2)
                return True

            return False

        except Exception as e:
            log.debug(f"[Outreach] Connection error: {e}")
            return False

    # ── Email fallback ────────────────────────────────────────────────────────

    async def _find_email(self, page, contact: dict, job: dict) -> str:
        # 1. Check LinkedIn profile contact info
        try:
            contact_info_url = contact["profile_url"].rstrip("/") + "/overlay/contact-info/"
            await page.goto(contact_info_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(1.5)
            text        = await page.locator("main").inner_text()
            email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
            if email_match:
                return email_match.group(0)
        except Exception:
            pass

        # 2. Pattern-guess from company name
        company      = job.get("company", "").lower()
        company_domain = re.sub(r"[^a-z0-9]", "", company)
        name_parts   = contact.get("name", "").lower().split()
        if len(name_parts) >= 2 and len(company_domain) >= 3:
            first, last = name_parts[0], name_parts[-1]
            return f"{first}.{last}@{company_domain}.com"

        return ""

    async def _send_email_outreach(self, job: dict, contact: dict, note: str, email: str) -> bool:
        sender_email    = os.getenv("EMAIL_ADDRESS")
        sender_password = os.getenv("EMAIL_APP_PASSWORD")
        if not sender_email or not sender_password:
            log.warning("[Outreach] EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set")
            return False

        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text      import MIMEText

            body = (
                f"{note}\n\n"
                f"{PROFILE.get('full_name','Candidate')}\n"
                f"{PROFILE.get('email','you@example.com')} | {PROFILE.get('phone_display', PROFILE.get('phone',''))} | {PROFILE.get('linkedin','')}"
            )
            msg             = MIMEMultipart()
            msg["From"]     = sender_email
            msg["To"]       = email
            msg["Subject"]  = f"Re: {job.get('title')} at {job.get('company')}"
            msg.attach(MIMEText(body, "plain"))

            def _send():
                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                    s.login(sender_email, sender_password)
                    s.sendmail(sender_email, email, msg.as_string())

            await asyncio.get_event_loop().run_in_executor(None, _send)
            return True
        except Exception as e:
            log.error(f"[Outreach] Email send failed: {e}")
            return False

    # ── Note generation ───────────────────────────────────────────────────────

    async def _generate_note(self, job: dict, contact: dict) -> str:
        grok       = self._get_grok()
        first_name = contact.get("name", "").split()[0]
        common_str = (
            "COMMON GROUND: " + ", ".join(contact["common"])
            if contact.get("common")
            else "No direct common ground — keep it genuine and specific to the role."
        )

        # Build prompt using PROFILE for the sender name/university where needed
        prompt = (
            f"Write a short LinkedIn connection note from {PROFILE.get('first_name','Candidate')} to {contact.get('name')} "
            f"({contact.get('title','')} at {job.get('company')}).\n\n"
            "TONE MODEL:\n"
            "\"Hi Brittany, I just applied for the Data Science Intern role and wanted to reach out directly "
            "since I saw you lead the team. Figured it was worth putting a face to the application. "
            f"I'm a CS student at {PROFILE.get('university','your university')} — that's the kind of work I want to be doing.\"\n\n"
            "RULES:\n"
            "- Start with \"Hi {first_name},\"\n"
            "- Under 300 characters TOTAL (LinkedIn hard limit)\n"
            "- 2-3 sentences max\n"
            "- Mention: just applied for {job_title} + one genuine reason OR common ground\n"
            f"- {common_str}\n"
            "- Casual, direct — not a recruiter pitch\n"
            "- No: \"passionate\", \"leverage\", \"would love to connect\", \"excited to\"\n\n"
            "Write only the note, nothing else:"
        ).replace("{job_title}", job.get("title", ""))

        try:
            note = await grok._chat(prompt, max_tokens=150)
            return note.strip()[:300]
        except Exception:
            return (
                f"Hi {first_name}, I just applied for the {job.get('title')} role at "
                f"{job.get('company')} and wanted to put a face to the application. "
                f"CS grad student at {PROFILE.get('university','your university')}, available on F-1 CPT."
            )[:300]
