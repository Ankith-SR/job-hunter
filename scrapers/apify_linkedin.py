# scrapers/apify_linkedin.py
"""
LinkedIn job scraper via the Apify curious_coder/linkedin-jobs-scraper actor.

⚠️  UNTESTED — The URL-field mapping depends on the actor's current output schema.
    Run with --dry-run first and inspect the logged sample keys to verify
    apply_url / easy_apply_url / external_url are being picked up correctly.
"""
from apify_client import ApifyClient
import logging
import os

log = logging.getLogger(__name__)

# Field names that different versions of the actor use for the external apply URL
_APPLY_URL_KEYS  = ["applyUrl", "apply_url", "jobPostingUrl", "jobUrl", "job_link", "url"]
_EASY_APPLY_KEYS = ["easyApplyUrl", "easy_apply_url", "linkedinEasyApply", "easyApply"]
_EXTERNAL_KEYS   = ["externalApplyUrl", "external_url", "companyApplyUrl", "atsUrl"]
_COMPANY_KEYS    = ["companyLinkedInUrl", "companyUrl", "company_url", "companyProfileUrl"]


class ApifyScraper:
    def __init__(self, api_token):
        if not api_token:
            raise ValueError("APIFY_API_TOKEN is missing. Check your .env file.")
        self.client = ApifyClient(api_token)

    def scrape_linkedin(self, search_url, max_items=20):
        actor_id = "curious_coder/linkedin-jobs-scraper"

        run_input = {
            "urls": [search_url],
            "maxItems": max_items,
        }

        log.info(f"📡 Requesting data from Apify for: {search_url}")

        try:
            run = self.client.actor(actor_id).call(run_input=run_input)

            # ── Robust dataset_id extraction ──────────────────────────────────
            dataset_id = getattr(run, "default_dataset_id", None) \
                      or getattr(run, "defaultDatasetId", None)

            if not dataset_id:
                if hasattr(run, "model_dump"):
                    dumped = run.model_dump()
                    dataset_id = (
                        dumped.get("defaultDatasetId")
                        or (dumped.get("data") or {}).get("defaultDatasetId")
                    )
                else:
                    try:
                        dumped = dict(run)
                        dataset_id = (
                            dumped.get("defaultDatasetId")
                            or (dumped.get("data") or {}).get("defaultDatasetId")
                        )
                    except Exception:
                        dataset_id = None

            if not dataset_id:
                log.error(
                    "Run object keys/attrs: %s",
                    vars(run) if hasattr(run, "__dict__") else run,
                )
                raise Exception("Could not find defaultDatasetId in run results")

            log.info("📦 Using dataset: %s", dataset_id)
            dataset = self.client.dataset(dataset_id)

            results = []
            for item in dataset.iterate_items():
                # ── Apply URL (generic / external ATS) ───────────────────────
                apply_url = _first(item, _APPLY_URL_KEYS)

                # ── LinkedIn Easy Apply URL ───────────────────────────────────
                # Some actor versions return a boolean; only keep if it's a URL
                easy_apply_raw = _first(item, _EASY_APPLY_KEYS)
                easy_apply_url = easy_apply_raw if isinstance(easy_apply_raw, str) and easy_apply_raw.startswith("http") else None
                # Fallback: if actor just flags easy-apply as True, reconstruct URL from jobId
                if not easy_apply_url and easy_apply_raw:
                    jid = item.get("jobId") or item.get("id") or item.get("job_id")
                    if jid:
                        easy_apply_url = f"https://www.linkedin.com/jobs/view/{jid}/"

                # ── Separate external company ATS URL ─────────────────────────
                external_url = _first(item, _EXTERNAL_KEYS)

                # ── Company LinkedIn page ─────────────────────────────────────
                company_li_url = _first(item, _COMPANY_KEYS)

                job_id = item.get("jobId") or item.get("id") or item.get("job_id") or item.get("jobIdStr")

                results.append({
                    "id":              job_id,
                    "title":           item.get("title") or item.get("jobTitle") or item.get("position"),
                    "company":         item.get("companyName") or item.get("company") or item.get("employer"),
                    "apply_url":       apply_url,
                    "easy_apply_url":  easy_apply_url,
                    "external_url":    external_url,
                    "company_li_url":  company_li_url,
                    "location":        item.get("location") or item.get("jobLocation"),
                    "description":     item.get("description") or item.get("jobDescription"),
                    "source":          "linkedin",
                    "raw":             item,   # keep for debugging
                })

            # ── Log a quick sample for debugging ─────────────────────────────
            if results:
                sample = results[0]
                log.info("Sample scraped item keys: %s", list(sample["raw"].keys())[:25])
                log.info(
                    "Sample URL fields — apply_url=%s | easy_apply_url=%s | external_url=%s",
                    sample["apply_url"], sample["easy_apply_url"], sample["external_url"],
                )
            log.info("✅ Successfully scraped %d LinkedIn jobs.", len(results))
            return results

        except Exception as e:
            log.error("💥 Apify LinkedIn Scraper Error: %s", e)
            return []


def _first(item: dict, keys: list):
    """Return the first non-empty value found for any of the given keys."""
    for k in keys:
        v = item.get(k)
        if v:
            return v
    return None
