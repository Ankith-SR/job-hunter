# scrapers/apify_handshake.py
"""
Handshake job scraper via the Apify orgupdate/handshake-jobs-scraper actor (async).

⚠️  UNTESTED — field names depend on the actor's current output schema.
    Run once with --dry-run and check the logs for sample keys before
    relying on apply_url / external_url extraction.
"""
import os
import asyncio
import json
import logging
from typing import List, Dict, Optional

import httpx

log = logging.getLogger(__name__)
APIFY_BASE = "https://api.apify.com/v2"

_APPLY_URL_KEYS   = ["applyUrl", "apply_url", "jobUrl", "url", "applicationUrl"]
_EXTERNAL_URL_KEYS = ["externalApplyUrl", "external_url", "companyApplyUrl", "atsUrl"]


class ApifyHandshakeScraper:
    """
    Async Apify runner for the orgupdate/handshake-jobs-scraper actor.
    Reads APIFY_TOKEN and APIFY_ACTOR_ID from environment by default.
    """

    def __init__(self, token: Optional[str] = None, actor_id: Optional[str] = None, timeout: int = 300):
        self.token = token or os.environ.get("APIFY_TOKEN")
        self.actor_id = actor_id or os.environ.get("APIFY_ACTOR_ID")
        if not self.token or not self.actor_id:
            raise RuntimeError("APIFY_TOKEN and APIFY_ACTOR_ID must be set in environment")
        self.timeout = timeout

    # ── URL helpers ───────────────────────────────────────────────────────────

    def _actor_runs_url(self) -> str:
        return f"{APIFY_BASE}/acts/{self.actor_id}/runs?token={self.token}"

    def _run_status_url(self, run_id: str) -> str:
        return f"{APIFY_BASE}/actor-runs/{run_id}?token={self.token}"

    def _dataset_items_url(self, dataset_id: str) -> str:
        return f"{APIFY_BASE}/datasets/{dataset_id}/items?format=json&clean=true&token={self.token}"

    # ── Apify API calls ───────────────────────────────────────────────────────

    async def start_run(self, input_payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(self._actor_runs_url(), json={"content": input_payload})
            r.raise_for_status()
            return r.json()

    async def wait_for_run_finish(self, run_id: str, poll_interval: float = 2.0) -> dict:
        deadline = asyncio.get_event_loop().time() + self.timeout
        async with httpx.AsyncClient(timeout=30.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                r = await client.get(self._run_status_url(run_id))
                r.raise_for_status()
                data = r.json()
                status = data.get("status")
                if status in ("SUCCEEDED", "FAILED", "ABORTED"):
                    log.info("[Apify] Run %s finished with status %s", run_id, status)
                    return data
                await asyncio.sleep(poll_interval)
        raise TimeoutError(f"Apify run {run_id} did not finish within {self.timeout} seconds")

    async def fetch_dataset_items(self, dataset_id: str) -> List[Dict]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(self._dataset_items_url(dataset_id))
            r.raise_for_status()
            text = r.text.strip()
            if not text:
                return []
            # Try as a JSON array first (clean=true usually gives this)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
            # Fall back to newline-delimited JSON
            items = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("[Apify] Skipping unparseable line in dataset response")
            return items

    # ── Main entry ────────────────────────────────────────────────────────────

    async def scrape(self, keywords: Optional[List[str]] = None, max_items: int = 100, use_proxy: bool = True) -> List[Dict]:
        if not keywords:
            keywords = ["software engineer intern"]
        input_payload = {
            "keywords": keywords,
            "maxItems": max_items,
            "useProxy": bool(use_proxy),
        }

        run_meta = await self.start_run(input_payload)
        run_id = (run_meta.get("data") or {}).get("id") or run_meta.get("id")
        if not run_id:
            raise RuntimeError("Failed to start Apify run; no run id returned")

        run_info = await self.wait_for_run_finish(run_id)
        dataset_id = (
            (run_info.get("data") or {}).get("defaultDatasetId")
            or (run_info.get("data") or {}).get("datasetId")
        )
        if not dataset_id:
            log.warning("[Apify] No dataset id found for run %s; returning empty list", run_id)
            return []

        items = await self.fetch_dataset_items(dataset_id)

        # ── Log a sample for debugging ──────────────────────────────────────
        if items:
            log.info("[Apify Handshake] Sample item keys: %s", list(items[0].keys())[:25])

        jobs = []
        for it in items:
            apply_url   = _first(it, _APPLY_URL_KEYS)
            external_url = _first(it, _EXTERNAL_URL_KEYS)

            jobs.append({
                "id":           it.get("id") or it.get("jobId") or (apply_url or "").split("/")[-1],
                "title":        it.get("title") or it.get("jobTitle") or "",
                "company":      it.get("company") or it.get("employer_name") or it.get("employerName") or "",
                "apply_url":    apply_url,
                "external_url": external_url,
                "url":          it.get("url") or apply_url or "",
                "location":     it.get("location") or "",
                "description":  it.get("description") or it.get("jobDescription") or "",
                "source":       "handshake",
                "raw":          it,
            })
        return jobs


def _first(item: dict, keys: list):
    for k in keys:
        v = item.get(k)
        if v:
            return v
    return None
