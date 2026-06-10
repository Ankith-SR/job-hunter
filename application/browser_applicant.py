from utils.profile import load_profile
PROFILE = load_profile()
# application/browser_applicant.py
"""
Browser-based job application automation.
Routes each job to the correct handler based on the apply URL:
  - linkedin.com/jobs  → LinkedIn Easy Apply
  - myworkdayjobs.com  → Workday
  - greenhouse.io      → Greenhouse
  - lever.co           → Lever
  - ashbyhq.com        → Ashby
  - everything else    → Generic (upload resume + log for manual review)

All LinkedIn automation reuses the persistent session saved by:
    python main.py --setup-session linkedin
"""

import asyncio
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Candidate profile — used to fill form fields automatically

class BrowserApplicant:
    def __init__(self, config: dict):
        self.config = config

    # ── Public entry point ────────────────────────────────────────────────────

    async def apply(self, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False) -> bool:
        """
        Route the job to the right ATS handler.
        If dry_run is True, fill forms and take screenshots but do not click final Submit.
        Returns True if the application was submitted (or partially completed).
        """
        url = job.get("apply_url") or job.get("url") or ""
        if not url:
            log.warning(f"[Browser] No apply URL for {job.get('title')} — skipping")
            return False

        handler, label = self._detect_ats(url)
        log.info(f"[Browser] Detected ATS: {label} for {url}")

        try:
            from utils.session_manager import SessionManager, SessionNotFoundError

            # LinkedIn uses the saved LinkedIn session
            if label == "LinkedIn Easy Apply":
                try:
                    context = await SessionManager.get_context("linkedin")
                except SessionNotFoundError as e:
                    log.error(str(e))
                    return False
            else:
                # All other ATS: launch a fresh non-persistent context
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                browser = await self._pw.chromium.launch(
                    headless=os.getenv("BROWSER_HEADLESS", "true").lower() == "true",
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )

            page = context.pages[0] if context.pages else await context.new_page()

            try:
                # pass dry_run to the handler
                result = await handler(page, job, resume_path, cover_letter_path, dry_run=dry_run)
                return result
            finally:
                if label == "LinkedIn Easy Apply":
                    await SessionManager.close_context(context)
                else:
                    await context.close()
                    if hasattr(self, "_pw"):
                        await self._pw.stop()

        except Exception as e:
            log.error(f"[Browser] {label} handler crashed: {e}", exc_info=True)
            return False

    # ── ATS detection ─────────────────────────────────────────────────────────

    def _detect_ats(self, url: str):
        """Return (handler_method, label) based on the apply URL."""
        u = url.lower()
        if "linkedin.com/jobs" in u or "linkedin.com/comm/jobs" in u:
            return self._handle_linkedin_easy_apply, "LinkedIn Easy Apply"
        if "myworkdayjobs.com" in u or "workday.com" in u:
            return self._handle_workday, "Workday"
        if "greenhouse.io" in u or "boards.greenhouse" in u:
            return self._handle_greenhouse, "Greenhouse"
        if "lever.co" in u:
            return self._handle_lever, "Lever"
        if "ashbyhq.com" in u:
            return self._handle_ashby, "Ashby"
        return self._handle_generic, "Generic"

    # ── LinkedIn Easy Apply ───────────────────────────────────────────────────

    async def _handle_linkedin_easy_apply(
        self, page, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False
    ) -> bool:
        """
        Full LinkedIn Easy Apply flow:
        1. Navigate to job page
        2. Click Easy Apply button
        3. Walk through modal pages: contact info → resume upload → work auth questions → review → submit
        """
        url = job.get("apply_url") or job.get("url", "")
        log.info(f"[LinkedIn] Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        # ── Step 1: Find and click Easy Apply button ──────────────────────────
        easy_apply_clicked = False
        for selector in [
            "button.jobs-apply-button",
            "button[aria-label*='Easy Apply']",
            "button:has-text('Easy Apply')",
            ".jobs-apply-button",
        ]:
            btn = page.locator(selector)
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click()
                easy_apply_clicked = True
                log.info("[LinkedIn] Clicked Easy Apply button")
                break

        if not easy_apply_clicked:
            log.warning(f"[LinkedIn] No Easy Apply button found — may be external apply or already applied")
            return False

        await asyncio.sleep(2)

        # ── Step 2: Walk the multi-step modal ────────────────────────────────
        max_steps = 12   # safety cap — most jobs are 3-6 steps
        step = 0

        while step < max_steps:
            step += 1
            log.info(f"[LinkedIn] Modal step {step}")

            # Check if we're done — "Application submitted" confirmation
            if await self._linkedin_check_submitted(page):
                log.info("[LinkedIn] ✅ Application submitted!")
                return True

            # Fill whatever fields are visible on this step
            await self._linkedin_fill_step(page, resume_path, cover_letter_path)

            # Try to advance: "Next", "Review", "Submit application"
            advanced = await self._linkedin_advance(page, dry_run=dry_run)
            if not advanced:
                log.warning(f"[LinkedIn] Could not advance past step {step} — stopping")
                return False

            await asyncio.sleep(1.5)

        log.warning("[LinkedIn] Hit max steps without submission")
        return False

    async def _linkedin_fill_step(self, page, resume_path: Path, cover_letter_path: Path = None):
        """Fill all visible fields on the current Easy Apply modal page."""

        # ── Resume upload ─────────────────────────────────────────────────────
        for upload_sel in [
            "input[type='file'][name*='resume']",
            "input[type='file'][accept*='pdf']",
            "input[type='file']",
        ]:
            uploader = page.locator(upload_sel)
            if await uploader.count() > 0:
                try:
                    await uploader.first.set_input_files(str(resume_path))
                    log.info("[LinkedIn] Uploaded resume")
                    break
                except Exception as e:
                    log.debug(f"[LinkedIn] Resume upload failed: {e}")

        # ── Cover letter upload or textarea ───────────────────────────────────
        if cover_letter_path and cover_letter_path.exists():
            cl_uploaders = page.locator("input[type='file']")
            count = await cl_uploaders.count()
            if count > 1:
                try:
                    await cl_uploaders.nth(1).set_input_files(str(cover_letter_path))
                    log.info("[LinkedIn] Uploaded cover letter")
                except Exception as e:
                    log.debug(f"[LinkedIn] Cover letter upload failed: {e}")

        # ── Text inputs — map label text to profile values ────────────────────
        field_map = {
            "first name":       PROFILE["first_name"],
            "last name":        PROFILE["last_name"],
            "email":            PROFILE["email"],
            "phone":            PROFILE["phone_display"],
            "mobile":           PROFILE["phone_display"],
            "city":             PROFILE["city"],
            "location":         PROFILE["location"],
            "linkedin":         PROFILE["linkedin"],
            "zip":              PROFILE["zip"],
            "postal":           PROFILE["zip"],
            "university":       PROFILE["university"],
            "school":           PROFILE["university"],
            "degree":           PROFILE["degree"],
            "major":            PROFILE["major"],
            "gpa":              "3.22",
            "graduation":       PROFILE["grad_year"],
            "grad year":        PROFILE["grad_year"],
            "salary":           "0",
            "desired salary":   "",
            "expected salary":  "",
        }

        inputs = page.locator("input[type='text'], input[type='email'], input[type='tel'], input[type='number']")
        count = await inputs.count()

        for i in range(count):
            inp = inputs.nth(i)
            try:
                if not await inp.is_visible():
                    continue

                # Try to find the label for this input
                label_text = await self._get_field_label(page, inp)
                label_lower = label_text.lower()

                for keyword, value in field_map.items():
                    if keyword in label_lower and value:
                        current = await inp.input_value()
                        if not current:
                            await inp.fill(value)
                            log.debug(f"[LinkedIn] Filled '{label_text}' → {value}")
                        break
            except Exception as e:
                log.debug(f"[LinkedIn] Input fill error: {e}")

        # ── Select / dropdown fields ──────────────────────────────────────────
        select_map = {
            "country":      PROFILE["country"],
            "state":        PROFILE["state_full"],
            "authorized":   PROFILE["authorized"],
            "sponsorship":  PROFILE["sponsorship"],
            "veteran":      PROFILE["veteran"],
            "disability":   PROFILE["disability"],
            "gender":       "Prefer not to say",
            "ethnicity":    "Prefer not to say",
            "race":         "Prefer not to say",
        }

        selects = page.locator("select")
        count = await selects.count()
        for i in range(count):
            sel = selects.nth(i)
            try:
                if not await sel.is_visible():
                    continue
                label_text = await self._get_field_label(page, sel)
                label_lower = label_text.lower()
                for keyword, value in select_map.items():
                    if keyword in label_lower:
                        await sel.select_option(label=value)
                        log.debug(f"[LinkedIn] Selected '{value}' for '{label_text}'")
                        break
            except Exception as e:
                log.debug(f"[LinkedIn] Select fill error: {e}")

        # ── Radio buttons — work authorization ────────────────────────────────
        radio_yes_patterns = [
            "label:has-text('Yes')",
            "label:has-text('yes')",
        ]
        auth_questions = page.locator(
            "fieldset:has-text('authorized'), fieldset:has-text('sponsorship'), "
            "fieldset:has-text('legally'), fieldset:has-text('eligible')"
        )
        fieldset_count = await auth_questions.count()
        for i in range(fieldset_count):
            fs = auth_questions.nth(i)
            try:
                text = (await fs.inner_text()).lower()
                if "sponsor" in text:
                    no_label = fs.locator("label:has-text('No')")
                    if await no_label.count() > 0:
                        await no_label.first.click()
                        log.debug("[LinkedIn] Answered sponsorship → No")
                else:
                    yes_label = fs.locator("label:has-text('Yes')")
                    if await yes_label.count() > 0:
                        await yes_label.first.click()
                        log.debug("[LinkedIn] Answered work auth → Yes")
            except Exception as e:
                log.debug(f"[LinkedIn] Radio fill error: {e}")

    async def _linkedin_advance(self, page, dry_run: bool = False) -> bool:
        """Click Next / Review / Submit — whichever is visible. Returns False if none found."""
        for selector, label in [
            ("button[aria-label='Submit application']",          "Submit"),
            ("button:has-text('Submit application')",            "Submit"),
            ("button[aria-label='Review your application']",     "Review"),
            ("button:has-text('Review')",                        "Review"),
            ("button[aria-label='Continue to next step']",       "Next"),
            ("button:has-text('Next')",                          "Next"),
            ("button:has-text('Continue')",                      "Continue"),
        ]:
            btn = page.locator(selector)
            if await btn.count() > 0 and await btn.first.is_visible():
                try:
                    # If this is the final Submit and we're in dry_run, do NOT click it.
                    if label == "Submit" and dry_run:
                        log.info("[LinkedIn] Dry run enabled — skipping final Submit")
                        try:
                            await page.screenshot(path="linkedin_dryrun_submit.png", full_page=False)
                            log.info("[LinkedIn] Saved screenshot linkedin_dryrun_submit.png")
                        except Exception:
                            log.debug("[LinkedIn] Screenshot failed")
                        return True
                    await btn.first.click()
                    log.info(f"[LinkedIn] Clicked: {label}")
                    return True
                except Exception as e:
                    log.debug(f"[LinkedIn] Click failed for {label}: {e}")

        return False

    async def _linkedin_check_submitted(self, page) -> bool:
        """Return True if the submission confirmation is visible."""
        for selector in [
            "h3:has-text('Application submitted')",
            "h1:has-text('Application submitted')",
            "div[aria-label='Application submitted']",
            ".artdeco-inline-feedback--success",
            ":has-text('Your application was sent')",
        ]:
            el = page.locator(selector)
            if await el.count() > 0 and await el.first.is_visible():
                return True
        return False

    async def _get_field_label(self, page, element) -> str:
        """Try multiple strategies to get the label text for a form element."""
        try:
            aria = await element.get_attribute("aria-label")
            if aria:
                return aria
        except Exception:
            pass

        try:
            el_id = await element.get_attribute("id")
            if el_id:
                label = page.locator(f"label[for='{el_id}']")
                if await label.count() > 0:
                    return await label.first.inner_text()
        except Exception:
            pass

        try:
            placeholder = await element.get_attribute("placeholder")
            if placeholder:
                return placeholder
        except Exception:
            pass

        try:
            text = await element.evaluate(
                """el => {
                    let node = el;
                    for (let i = 0; i < 5; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        const label = node.querySelector('label, legend, .fb-dash-form-element__label, .jobs-easy-apply-form-element__label');
                        if (label) return label.innerText;
                    }
                    return '';
                }"""
            )
            if text:
                return text.strip()
        except Exception:
            pass

        return ""

    # ── Workday ───────────────────────────────────────────────────────────────

    async def _handle_workday(
        self, page, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False
    ) -> bool:
        url = job.get("apply_url", "")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        try:
            await page.wait_for_selector("[data-automation-id='applyButton']", timeout=15000)
            await page.click("[data-automation-id='applyButton']")
            await asyncio.sleep(2)
        except Exception:
            log.warning("[Workday] Apply button not found")
            return False

        uploader = page.locator("input[type='file']")
        if await uploader.count() > 0:
            await uploader.first.set_input_files(str(resume_path))
            await asyncio.sleep(1)
            if cover_letter_path and cover_letter_path.exists() and await uploader.count() > 1:
                await uploader.nth(1).set_input_files(str(cover_letter_path))

        try:
            await page.wait_for_selector(
                "[data-automation-id='bottom-navigation-next-button']", timeout=10000
            )
            await page.click("[data-automation-id='bottom-navigation-next-button']")
            log.info("[Workday] Advanced past first page — stopping for manual review")
        except Exception:
            pass

        # Workday flows are too varied for full automation — stop here and log
        if dry_run:
            try:
                await page.screenshot(path="workday_dryrun.png")
                log.info("[Workday] Saved screenshot workday_dryrun.png")
            except Exception:
                pass
        log.info(f"[Workday] Partial completion. Manual review needed: {url}")
        return True

    # ── Greenhouse ────────────────────────────────────────────────────────────

    async def _handle_greenhouse(
        self, page, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False
    ) -> bool:
        url = job.get("apply_url", "")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        # Resume upload
        for sel in ["input#resume", "input[name='resume']", "input[type='file']"]:
            uploader = page.locator(sel)
            if await uploader.count() > 0:
                await uploader.first.set_input_files(str(resume_path))
                log.info("[Greenhouse] Uploaded resume")
                break

        # Cover letter
        if cover_letter_path and cover_letter_path.exists():
            cl = page.locator("input#cover_letter, input[name='cover_letter']")
            if await cl.count() > 0:
                await cl.first.set_input_files(str(cover_letter_path))

        # Standard fields
        await self._fill_standard_fields(page, {
            "first_name":  PROFILE["first_name"],
            "last_name":   PROFILE["last_name"],
            "email":       PROFILE["email"],
            "phone":       PROFILE["phone_display"],
            "linkedin_profile_url": PROFILE["linkedin"],
        })

        # Submit
        submit = page.locator("input[type='submit'], button[type='submit'], button:has-text('Submit')")
        if await submit.count() > 0:
            if dry_run:
                try:
                    await page.screenshot(path="greenhouse_dryrun.png")
                    log.info("[Greenhouse] Dry run — saved screenshot greenhouse_dryrun.png")
                except Exception:
                    pass
                return True
            await submit.first.click()
            await asyncio.sleep(2)
            log.info("[Greenhouse] Submitted")
            return True

        log.warning("[Greenhouse] Could not find submit button")
        return False

    # ── Lever ─────────────────────────────────────────────────────────────────

    async def _handle_lever(
        self, page, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False
    ) -> bool:
        url = job.get("apply_url", "")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        uploader = page.locator("input[type='file']")
        if await uploader.count() > 0:
            await uploader.first.set_input_files(str(resume_path))
            log.info("[Lever] Uploaded resume")

        await self._fill_standard_fields(page, {
            "name":    PROFILE["full_name"],
            "email":   PROFILE["email"],
            "phone":   PROFILE["phone_display"],
            "org":     PROFILE["university"],
            "linkedin": PROFILE["linkedin"],
        })

        if cover_letter_path and cover_letter_path.exists():
            cl_text = page.locator("textarea[name*='cover'], textarea[placeholder*='cover']")
            if await cl_text.count() > 0:
                try:
                    text = cover_letter_path.read_text(encoding="utf-8", errors="ignore")
                    await cl_text.first.fill(text[:3000])
                except Exception:
                    pass

        submit = page.locator("button[type='submit'], input[type='submit']")
        if await submit.count() > 0:
            if dry_run:
                try:
                    await page.screenshot(path="lever_dryrun.png")
                    log.info("[Lever] Dry run — saved screenshot lever_dryrun.png")
                except Exception:
                    pass
                return True
            await submit.first.click()
            await asyncio.sleep(2)
            log.info("[Lever] Submitted")
            return True

        log.warning("[Lever] Could not find submit button")
        return False

    # ── Ashby ─────────────────────────────────────────────────────────────────

    async def _handle_ashby(
        self, page, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False
    ) -> bool:
        url = job.get("apply_url", "")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        uploader = page.locator("input[type='file']")
        if await uploader.count() > 0:
            await uploader.first.set_input_files(str(resume_path))
            log.info("[Ashby] Uploaded resume")

        await self._fill_standard_fields(page, {
            "firstName":   PROFILE["first_name"],
            "lastName":    PROFILE["last_name"],
            "email":       PROFILE["email"],
            "phone":       PROFILE["phone_display"],
            "linkedinUrl": PROFILE["linkedin"],
        })

        submit = page.locator("button[type='submit'], button:has-text('Submit Application')")
        if await submit.count() > 0:
            if dry_run:
                try:
                    await page.screenshot(path="ashby_dryrun.png")
                    log.info("[Ashby] Dry run — saved screenshot ashby_dryrun.png")
                except Exception:
                    pass
                return True
            await submit.first.click()
            await asyncio.sleep(2)
            log.info("[Ashby] Submitted")
            return True

        log.warning("[Ashby] Could not find submit button")
        return False

    # ── Generic fallback ──────────────────────────────────────────────────────

    async def _handle_generic(
        self, page, job: dict, resume_path: Path, cover_letter_path: Path = None, dry_run: bool = False
    ) -> bool:
        url = job.get("apply_url", "")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2)

        uploaded = False
        uploader = page.locator("input[type='file']").first
        if await uploader.count() > 0:
            try:
                await uploader.set_input_files(str(resume_path))
                uploaded = True
                log.info("[Generic] Uploaded resume")
            except Exception as e:
                log.debug(f"[Generic] Upload failed: {e}")

        if dry_run:
            try:
                await page.screenshot(path="generic_dryrun.png")
                log.info("[Generic] Dry run — saved screenshot generic_dryrun.png")
            except Exception:
                pass

        log.info(f"[Generic] Partial fill complete. Manual review needed: {url}")
        return uploaded

    # ── Shared helper — fill fields by name/id ────────────────────────────────

    async def _fill_standard_fields(self, page, fields: dict):
        """Fill input fields by matching name or id attributes to the fields dict."""
        for attr_value, value in fields.items():
            if not value:
                continue
            for attr in ["name", "id"]:
                inp = page.locator(f"input[{attr}='{attr_value}']")
                if await inp.count() > 0:
                    try:
                        current = await inp.first.input_value()
                        if not current:
                            await inp.first.fill(value)
                            log.debug(f"[Form] Filled [{attr}={attr_value}] → {value}")
                    except Exception as e:
                        log.debug(f"[Form] Fill error for {attr_value}: {e}")
                    break

