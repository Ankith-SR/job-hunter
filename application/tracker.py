"""
Application tracker — stores processed jobs in logs/applied.json.
v2: tracks richer status (pending / applied / rejected / interviewing / offer).
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

TRACKER_FILE = Path("logs/applied.json")

VALID_STATUSES = {"pending", "applied", "rejected", "interviewing", "offer", "withdrawn"}


class ApplicationTracker:
    def __init__(self):
        TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    def already_applied(self, job_id: str) -> bool:
        return job_id in self._data

    def mark_applied(self, job: dict, status: str = "pending"):
        if status not in VALID_STATUSES:
            status = "pending"
        job_id = job.get("id") or job.get("url") or ""
        self._data[job_id] = {
            "title":       job.get("title", ""),
            "company":     job.get("company", ""),
            "source":      job.get("source", ""),
            "status":      status,
            "applied_at":  datetime.now().isoformat(),
            "url":         job.get("url") or job.get("apply_url", ""),
            "easy_apply_url": job.get("easy_apply_url", ""),
            "external_url":   job.get("external_url", ""),
            "handshake_url":  job.get("url", "") if job.get("source") == "handshake" else "",
            "resume_path":    job.get("resume_path", ""),
            "cover_letter_path": job.get("cover_letter_path", ""),
            "email_draft_link":  job.get("email_draft_link", ""),
            "contacts":    job.get("contacts", []),
        }
        self._save()
        log.debug("[Tracker] Marked %s: %s", status, job_id)

    def update_status(self, job_id: str, status: str):
        if job_id not in self._data:
            log.warning("[Tracker] Unknown job id: %s", job_id)
            return
        if status not in VALID_STATUSES:
            log.warning("[Tracker] Invalid status '%s'", status)
            return
        self._data[job_id]["status"] = status
        self._data[job_id]["updated_at"] = datetime.now().isoformat()
        self._save()

    def get_all(self) -> dict:
        return dict(self._data)

    def stats(self) -> dict:
        total    = len(self._data)
        by_src   = {}
        by_status = {}
        for v in self._data.values():
            src = v.get("source", "unknown")
            by_src[src] = by_src.get(src, 0) + 1
            st = v.get("status", "pending")
            by_status[st] = by_status.get(st, 0) + 1
        return {"total": total, "by_source": by_src, "by_status": by_status}

    def _load(self) -> dict:
        if TRACKER_FILE.exists():
            try:
                return json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
            except Exception:
                log.warning("[Tracker] Could not read tracker file — starting fresh")
        return {}

    def _save(self):
        TRACKER_FILE.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
