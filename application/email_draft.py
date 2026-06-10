# application/email_draft.py
"""
EmailDraftSaver — saves application emails as Gmail drafts (via Gmail API)
or falls back to a local .eml file in the job folder.

Gmail API setup (one-time):
  1. Enable Gmail API at https://console.cloud.google.com/
  2. Create OAuth 2.0 credentials (Desktop app), download as credentials.json
  3. Place credentials.json in the project root
  4. On first run, a browser window will open for you to authorise access
  5. The token is saved to output/gmail_token.json and reused on future runs

If credentials.json is absent, or if Gmail API fails, the draft is written
as  <job_folder>/email_draft.eml  which you can open in any email client.
"""

import asyncio
import base64
import json
import logging
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

GMAIL_TOKEN_PATH       = Path("output/gmail_token.json")
GMAIL_CREDENTIALS_PATH = Path("credentials.json")
GMAIL_SCOPES           = ["https://www.googleapis.com/auth/gmail.compose"]


class EmailDraftSaver:
    def __init__(self, config=None):
        self.config   = config or {}
        self.email    = os.getenv("EMAIL_ADDRESS")
        self.gmail_svc = None  # lazy-loaded

    # ── Public API ─────────────────────────────────────────────────────────────

    async def save_draft(
        self,
        job: dict,
        resume_pdf: Path,
        cover_pdf: Path,
        job_folder: Path,
    ) -> Optional[str]:
        """
        Build the application email, save it as a Gmail draft or local .eml,
        and return a link/path the user can click.
        """
        recruiter_email = job.get("recruiter_email")
        if not recruiter_email:
            log.info("[EmailDraft] No recruiter email for %s — skipping draft", job.get("title"))
            return None

        try:
            from ai.grok_client import GrokClient
            from utils.profile import load_profile
            profile      = load_profile()
            grok         = GrokClient()
            email_content = await grok.generate_email_body(job, profile.get("full_name", "Applicant"))
        except Exception as e:
            log.warning("[EmailDraft] Could not generate email body: %s — using fallback", e)
            email_content = {
                "subject": f"Application for {job.get('title', 'the role')} at {job.get('company', '')}",
                "body":    "Dear Hiring Team,\n\nPlease find my application materials attached.\n\nBest regards",
            }

        msg = self._build_mime(
            to      = recruiter_email,
            subject = email_content["subject"],
            body    = email_content["body"],
            attachments = [p for p in [resume_pdf, cover_pdf] if p and p.exists()],
        )

        # Try Gmail API first
        try:
            link = await asyncio.get_event_loop().run_in_executor(
                None, self._save_gmail_draft, msg
            )
            if link:
                log.info("[EmailDraft] Gmail draft saved → %s", link)
                return link
        except Exception as e:
            log.warning("[EmailDraft] Gmail API failed (%s) — falling back to .eml", e)

        # Fallback: write local .eml file
        eml_path = job_folder / "email_draft.eml"
        eml_path.write_bytes(msg.as_bytes())
        log.info("[EmailDraft] Saved local draft → %s", eml_path)
        return str(eml_path)

    # ── Gmail API ──────────────────────────────────────────────────────────────

    def _get_gmail_service(self):
        """Lazy-load and cache the Gmail API service."""
        if self.gmail_svc is not None:
            return self.gmail_svc

        if not GMAIL_CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"Gmail credentials not found at {GMAIL_CREDENTIALS_PATH}. "
                "See application/email_draft.py docstring for setup instructions."
            )

        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            creds = None
            if GMAIL_TOKEN_PATH.exists():
                creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), GMAIL_SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(GMAIL_CREDENTIALS_PATH), GMAIL_SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                GMAIL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
                GMAIL_TOKEN_PATH.write_text(creds.to_json())

            self.gmail_svc = build("gmail", "v1", credentials=creds)
            return self.gmail_svc

        except ImportError:
            raise ImportError(
                "Google API libraries not installed. "
                "Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )

    def _save_gmail_draft(self, msg: MIMEMultipart) -> Optional[str]:
        """Save msg as a Gmail draft and return the Gmail web URL."""
        svc = self._get_gmail_service()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        draft_id = draft.get("id", "")
        # Link directly to the draft in Gmail
        return f"https://mail.google.com/mail/u/0/#drafts/{draft_id}" if draft_id else "https://mail.google.com/mail/u/0/#drafts"

    # ── MIME helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_mime(to: str, subject: str, body: str, attachments: list[Path]) -> MIMEMultipart:
        msg = MIMEMultipart()
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        for path in attachments:
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(part)
        return msg
