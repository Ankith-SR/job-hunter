"""
Email applicant — sends job applications via Gmail SMTP.
"""

import asyncio
import logging
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger(__name__)

class EmailApplicant:
    def __init__(self, config=None):
        self.config = config
        self.email = os.getenv("EMAIL_ADDRESS")
        self.password = os.getenv("EMAIL_APP_PASSWORD")
        self.protocol = os.getenv("MAIL_PROTOCOL", "gmail.com")
        self.enabled = bool(self.email and self.password)

    async def apply(self, job: dict, resume_pdf: Path, cover_letter_pdf: Path) -> bool:
        if not self.enabled or not job.get("recruiter_email"):
            return False
        try:
            from ai.grok_client import GrokClient
            grok = GrokClient()
            from utils.profile import load_profile as _lp; _profile = _lp()
            email_content = await grok.generate_email_body(job, _profile.get("full_name", "Applicant"))
            msg = MIMEMultipart()
            msg["From"] = self.email
            msg["To"] = job["recruiter_email"]
            msg["Subject"] = email_content["subject"]
            msg.attach(MIMEText(email_content["body"], "plain"))
            
            for path in [resume_pdf, cover_letter_pdf]:
                if path.exists():
                    with open(path, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={path.name}")
                    msg.attach(part)
            
            await asyncio.get_event_loop().run_in_executor(None, self._send_smtp, msg, job["recruiter_email"])
            return True
        except Exception as e:
            log.error(f"[Email] Failed: {e}")
            return False

    def _send_smtp(self, msg, to):
        with smtplib.SMTP_SSL(f"smtp.{self.protocol}", 465) as s:
            s.login(self.email, self.password)
            s.sendmail(self.email, to, msg.as_string())