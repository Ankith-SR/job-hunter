from utils.profile import load_profile
PROFILE = load_profile()

"""
AI client — all LLM calls go through here.
Uses Google Gemini API (OpenAI-compatible endpoint).
Get a free key at aistudio.google.com — 1500 requests/day free.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

GROK_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "meta/llama-3.3-70b-instruct"


class GrokClient:
    def __init__(self):
        self.api_key = os.getenv("GROK_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise EnvironmentError(
                "GROK_API_KEY not set. Add your Groq key to .env as GROK_API_KEY=gsk_..."
            )
        self.model = os.getenv("GROK_MODEL", DEFAULT_MODEL)
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ──────────────────────────────────────────────────────────────────────
    # Job scoring
    # ──────────────────────────────────────────────────────────────────────

    async def batch_score_jobs(self, jobs: list[dict], resume_summaries: str) -> list[float]:
        """Score a batch of jobs against the candidate's resume summaries (0.0–1.0)."""
        prompt = f"""You are a career advisor helping a student find an internship or part-time role.

CANDIDATE PROFILE:
- Name: {PROFILE.get('full_name', 'Candidate')}
- Status: International student on F-1 visa (requires CPT/OPT sponsorship)
- Degree: {PROFILE.get('degree', 'M.S. Computer Science')} at {PROFILE.get('university', 'university')}, graduating May {PROFILE.get('grad_year', '2027')}
- Skills: Python, JavaScript, Java, React, Node.js, TensorFlow, PyTorch, Scikit-learn, AWS, Azure
- Experience: Full-stack web dev (MERN), ML pipelines, NLP, embedded systems
- Looking for: Internship or part-time SWE/data/ML roles in {PROFILE.get('city', 'local area')} or remote
- NOT looking for: Full-time roles, roles requiring US citizenship/green card, senior roles (5+ years exp)

RESUME SUMMARY:
{resume_summaries}

JOBS TO SCORE (JSON list):
{json.dumps([{"id": j["id"], "title": j["title"], "company": j["company"], "description": (j.get("description") or j.get("teaser") or "")[:500]} for j in jobs], indent=2)}

SCORING RULES:
- Score HIGH (0.7-1.0): internship/part-time, SWE/data/ML roles, entry-level, open to F-1/OPT, {PROFILE.get('city', 'local')} or remote
- Score MEDIUM (0.4-0.6): full-time but entry-level and matches skills well
- Score LOW (0.0-0.3): requires US citizenship/green card, senior level (5+ years), unrelated field, on-site only outside {PROFILE.get('city', 'local area')}

Return ONLY a JSON array of floats between 0.0 and 1.0, one score per job, in the same order.
Example: [0.85, 0.42, 0.91]
"""
        response = await self._chat(prompt, max_tokens=200)
        try:
            match = re.search(r"\[[\d.,\s]+\]", response)
            if match:
                scores = json.loads(match.group(0))
                if len(scores) == len(jobs):
                    return [max(0.0, min(1.0, float(s))) for s in scores]
        except Exception as e:
            log.warning(f"[Grok] Score parsing error: {e} — response: {response[:200]}")

        return [0.5] * len(jobs)

    # ──────────────────────────────────────────────────────────────────────
    # Resume selection
    # ──────────────────────────────────────────────────────────────────────

    async def select_best_resume(self, job: dict, resume_options: list[dict]) -> str:
        """Returns the filename of the best resume for this job."""
        options_text = "\n".join(
            f"{i+1}. {r['name']}: {r['summary'][:300]}"
            for i, r in enumerate(resume_options)
        )
        prompt = f"""You are a career advisor. Which resume best fits this job?

JOB: {job['title']} at {job['company']}
DESCRIPTION EXCERPT: {(job.get('description') or '')[:600]}

RESUME OPTIONS:
{options_text}

Reply with ONLY the number (1, 2, 3 etc.) of the best resume. Nothing else."""

        response = await self._chat(prompt, max_tokens=10)
        try:
            idx = int(re.search(r"\d+", response).group(0)) - 1
            return resume_options[max(0, min(idx, len(resume_options) - 1))]["name"]
        except Exception:
            return resume_options[0]["name"]

    # ──────────────────────────────────────────────────────────────────────
    # Resume tailoring
    # ──────────────────────────────────────────────────────────────────────

    async def tailor_resume(self, resume_text: str, job: dict) -> str:
        """Rewrite resume to match job. Returns tailored plain text."""
        prompt = f"""You are an expert resume writer and ATS specialist. Tailor this resume for the job below.

CONTENT RULES:
1. Mirror the job description's exact keywords and phrases naturally in bullet points — ATS systems match on these
2. Every bullet must start with a strong action verb (Built, Designed, Deployed, Optimised, Led, Reduced, Achieved, Engineered)
3. Quantify every bullet that can be quantified — keep existing numbers, add plausible ones where missing
4. Keep all dates, job titles, company names, and education entries exactly as-is — never change facts
5. Never invent experience or credentials
6. Expand thin roles/projects to 2-3 tight bullets using skills already present in the resume
7. Aim for 28-35 bullets total across all sections so the page looks full, not sparse
8. Remove the Languages & Volunteer section — use that space for stronger content
9. American English spelling only

FORMAT RULES (the PDF renderer parses this exactly — follow precisely):
- Output ONLY the resume text, zero commentary, no markdown, no backticks
- Contact header: candidate's full name on line 1, then contact details on line 2 separated by |
- Section headers on their own line, exactly: Education, Technical Skills, Experience, Projects & Research, Certifications & Achievements
- Entry headers (job/project titles with dates): put the date at the end separated by 2+ spaces
  Example:  Full-Stack Web Developer — RIDES (Bike Showroom Web App)  Aug – Dec 2023
- Bullets: start with • character
- Skills: Label: value1, value2 (bold label, values after colon)
- Do NOT use | to separate title from date — use 2+ spaces only

JOB TO TARGET:
Title: {job.get('title', 'Unknown')}
Company: {job.get('company', 'Unknown')}
Description: {job.get('description', job.get('teaser', ''))[:1500]}

CANDIDATE'S CURRENT RESUME:
{resume_text}

Output the tailored resume now:"""
        return await self._chat(prompt, max_tokens=2000)

    # ──────────────────────────────────────────────────────────────────────
    # Cover letter generation
    # ──────────────────────────────────────────────────────────────────────

    async def generate_cover_letter(self, job: dict, resume_text: str, candidate_name: str) -> str:
        """Generate a cover letter. Returns plain text body only."""
        prompt = f"""Write a short, casual cover letter body. Not a formal letter — more like a confident note from someone who actually read the job posting and has something real to say.

TONE MODEL — write like this:
"I just applied for the [role] and wanted to put a face to the application. I'm a CS student at ASU who's been building [relevant thing]. I like that this role sits at [specific intersection from the job description] — that's the kind of work I want to be doing. My resume is attached. Happy to chat if it's a fit."

STRICT RULES:
- Exactly 3 short paragraphs, under 250 words total
- Paragraph 1: Just applied + one genuine observation about what makes THIS role or THIS company interesting (be specific, not flattering)
- Paragraph 2: One or two things from the resume that are actually relevant — drop them naturally, don't list them like a bullet point narration
- Paragraph 3: 3 sentences max — close easy, mention F-1 CPT authorization once, don't beg
- Tone: casual, direct, a little personality. Like a smart person talking, not a cover letter template
- American English
- Do NOT start with "I am writing" or "I am excited" or "I wanted to reach out"
- Do NOT use: "passionate", "leverage", "synergy", "dynamic", "would love", "I believe", "I feel"
- Do NOT include date, address, greeting, sign-off — just the 3 paragraphs
- Vary sentence length — short punchy sentences mixed with longer ones

CANDIDATE: {candidate_name}
STATUS: {PROFILE.get('degree', 'Graduate')} student at {PROFILE.get('university', 'university')}, graduating May {PROFILE.get('grad_year', '2027')}, authorized to work on F-1 CPT
SKILLS: Python, JavaScript, React, Node.js, TensorFlow, PyTorch, AWS, Azure, REST APIs
JOB: {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')}
JOB DESCRIPTION: {job.get('description', job.get('teaser', ''))[:1000]}
RESUME HIGHLIGHTS: {resume_text[:1500]}

Write the 3 paragraphs now:"""

        return await self._chat(prompt, max_tokens=800)

    # ──────────────────────────────────────────────────────────────────────
    # Email body generation
    # ──────────────────────────────────────────────────────────────────────

    async def generate_email_body(self, job: dict, candidate_name: str) -> dict:
        """Generate a human, personal email body and subject for a job application."""
        first_name = PROFILE.get('first_name', candidate_name.split()[0] if candidate_name else 'Candidate')
        contact_line = (
            f"{PROFILE.get('email', '')}"
            f" | {PROFILE.get('phone_display', PROFILE.get('phone', ''))}"
            f" | {PROFILE.get('linkedin', '')}"
        )

        prompt = f"""Write a short outreach email from a job applicant to a hiring manager or team. Not a cover letter — more like a real person putting a face to an application.

TONE MODEL — aim for something like this:
"Hi Brittany,

I just applied for the Data Science Intern role and wanted to reach out directly since I saw you lead the team. Figured it was worth putting a face to the application.

I'm a CS student at {PROFILE.get('university', 'university')} who genuinely enjoys the customer-facing side of things. I like that this role sits at the intersection of analytics and actually helping people get value from a product. That's the kind of work I want to be doing.

Just wanted to say hi and let you know I'm excited about the opportunity.

{first_name}"

RULES:
- 3-4 short sentences in the body. Casual but not sloppy.
- Open: just applied + a specific reason they reached out (not generic flattery)
- Middle: one real thing about the candidate OR one specific thing about the role that resonated — keep it natural
- Close: warm but not desperate. Don't beg, don't oversell.
- Start with "Hi [first name]," if you can infer it, otherwise "Hi [company] team,"
- Sign off exactly: "{first_name}\\n\\n{contact_line}"
- Do NOT use: "would love", "passionate", "leverage", "excited to", "I am writing", "I believe", "please find attached"
- Every email must feel different — vary the angle, the specific detail, the rhythm
- American English

CANDIDATE: {candidate_name}
{PROFILE.get('degree', 'Graduate')} student at {PROFILE.get('university', 'university')} (May {PROFILE.get('grad_year', '2027')}), built full-stack apps (MERN), ML pipelines (TensorFlow/PyTorch),
2nd place hackathon for NLP sentiment analysis, published book chapter on AI/IoT, authorized on F-1 CPT

ROLE: {job.get('title', 'the role')} at {job.get('company', 'the company')}
JOB DESCRIPTION: {job.get('description', job.get('teaser', ''))[:800]}

Return ONLY valid JSON: {{"subject": "...", "body": "..."}}. No markdown, no backticks."""

        response = await self._chat(prompt, max_tokens=600)
        try:
            clean = response.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except Exception:
            return {
                "subject": f"{job.get('title')} Application — {candidate_name}",
                "body": (
                    f"Hi,\n\nI applied for the {job.get('title')} role at {job.get('company')} "
                    f"and wanted to reach out directly. My resume and cover letter are attached — "
                    f"I'd love the chance to chat.\n\nBest,\n{first_name}\n\n{contact_line}"
                )
            }

    async def generate_cover_letter_pdf(self, job: dict, resume) -> Path:
        """Convenience method — generate cover letter text and save as PDF."""
        from resume.resume_builder import ResumeBuilder
        builder = ResumeBuilder(self)
        return await builder.build_cover_letter(job, resume)

    # ──────────────────────────────────────────────────────────────────────
    # Core HTTP call
    # ──────────────────────────────────────────────────────────────────────

    async def _chat(self, prompt: str, max_tokens: int = 1000) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        timeout = httpx.Timeout(
            connect=30.0,
            read=300.0,
            write=30.0,
            pool=30.0,
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(3):
                try:
                    log.info(
                        f"[Grok] Sending request "
                        f"(model={self.model}, "
                        f"max_tokens={max_tokens}, "
                        f"prompt_chars={len(prompt)})"
                    )
                    resp = await client.post(
                        f"{GROK_BASE_URL}/chat/completions",
                        headers=self.headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait = 2 ** attempt * 30
                        log.warning(f"[Grok] Rate limited — waiting {wait}s …")
                        await asyncio.sleep(wait)
                    else:
                        log.error(f"[Grok] HTTP error: {e}")
                        raise
                except Exception as e:
                    if attempt == 2:
                        raise
                    log.warning(
                        f"[Grok] Attempt {attempt+1} failed: "
                        f"{type(e).__name__}: {str(e)}",
                        exc_info=True,
                    )
                    await asyncio.sleep(2)
        return ""
