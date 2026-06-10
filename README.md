# 🎯 job-hunter v2

An automated job application **preparation** pipeline for students and early-career candidates.

Scrapes job listings from LinkedIn and Handshake, scores them against your resumes, tailors a resume and cover letter for each one, saves a Gmail draft ready to send, and opens a review dashboard so **you** decide what to submit — no blind auto-applying.

> Built by a CS grad student who got tired of copy-pasting.  
> Open sourced so others can use it too.
>
> Questions or suggestions? Reach out:  
> 📧 ankithsr20@gmail.com  
> 📱 +1 602-697-4653

---

## What's new in v2 (vs v1)

### Major changes
| Area | v1 | v2 |
|---|---|---|
| Application flow | Auto-submitted (browser + email) | Prepares everything, **you** submit |
| Email | Auto-sent via SMTP | Saved as **Gmail draft** (or `.eml` fallback) |
| Output structure | Flat `output/resumes/` and `output/cover_letters/` | **Per-job folders**: `output/jobs/<date>_<company>_<role>/` |
| Dashboards | 1 (history only) | **2**: per-run review + cumulative history tracker |
| LinkedIn session | Required (Playwright login) | **Not needed** — no browser automation |
| LinkedIn outreach | Auto-sent connection requests | Removed from default pipeline |
| Apify URL extraction | Single fallback chain | **Multi-key fallback** for `easy_apply_url`, `external_url`, `company_li_url` |

### Removed
- `utils/session_manager.py` — no longer required
- `application/browser_applicant.py` — kept in repo for reference but not called by default pipeline
- `application/linkedin_outreach.py` — kept for reference
- Playwright dependency (unless you re-enable browser applicant manually)

### Added
- `application/email_draft.py` — saves to Gmail drafts via Gmail API; falls back to `.eml`
- `dashboard_run.py` — per-run review dashboard with all apply links + file previews
- Per-job output folders — everything for one job lives in one place
- Status tracking in the history dashboard (pending → applied → interviewing → offer / rejected)

> ⚠️ **Apify scraping is untested.** The URL-field extraction (`easy_apply_url`, `external_url`, etc.) uses a multi-key fallback to handle different actor output schemas, but you should run with `--dry-run` first and check the logs for `Sample scraped item keys` to verify the fields are being picked up correctly before spending Apify credits on a full run.

---

## What it does

1. **Scrapes jobs** from LinkedIn and Handshake via Apify (no browser login required)
2. **Scores each job** against your resume variants using an LLM — filters out irrelevant ones
3. **Selects the best resume** from your `materials/resumes/` folder
4. **Tailors the resume** for the specific job (ATS keyword optimisation)
5. **Generates a cover letter** in your voice
6. **Saves an email draft** to Gmail (or a local `.eml` file) with resume + cover letter attached
7. **Writes a per-job folder** with resume, cover letter, and job info JSON
8. **Opens a review dashboard** — you see all links (Easy Apply, Handshake, company ATS, email draft, files) and decide what to submit

---

## Requirements

- Python 3.11+
- A free LLM API key — default uses [NVIDIA NIM](https://integrate.api.nvidia.com) (free, Llama 3.3 70B). Groq and OpenAI also work.
- An [Apify](https://apify.com) account (free tier) for scraping

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/job-hunter.git
cd job-hunter
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure your profile

```bash
cp utils/profile.example.json utils/profile.json
```

Edit `utils/profile.json` with your details (name, email, phone, university, etc.).

### 3. Set your API keys

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM — free tier at https://integrate.api.nvidia.com
GROK_API_KEY=nvapi-...
# GROK_MODEL=meta/llama-3.3-70b-instruct   # default

# Apify — for scraping LinkedIn and Handshake
APIFY_API_TOKEN=apify_api_...
APIFY_ACTOR_ID=orgupdate/handshake-jobs-scraper   # Handshake actor

# Gmail draft saving (optional — see Gmail API setup below)
EMAIL_ADDRESS=you@gmail.com
```

### 4. Add your resume(s)

Drop your base resume PDFs in `materials/resumes/`. The AI picks the best one per job and tailors it from there. You can have multiple variants (e.g. one for SWE, one for data science).

```
materials/resumes/
  resume_software_engineer.pdf
  resume_data_science.pdf
```

### 5. Gmail API setup (optional but recommended)

This lets email drafts go straight to your Gmail Drafts folder with attachments.  
If you skip this, drafts are saved as `.eml` files in each job's output folder.

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Create a project → Enable the **Gmail API**
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
4. Application type: **Desktop app** → Download as `credentials.json`
5. Place `credentials.json` in the project root
6. On first run a browser window opens for you to authorise. The token is saved to `output/gmail_token.json` and reused automatically.

---

## Running

```bash
# Dry run — scrapes and scores but skips file generation
python main.py --dry-run

# Full run — generates resumes, cover letters, and email drafts
python main.py

# Mock run — no Apify calls, uses fake job data (safe for testing)
python main.py --mock
```

### Options

```
--dry-run           Score and select but skip file generation
--mock              Use mock job data instead of Apify (no API calls)
--boards            Which boards to scrape: handshake linkedin (default: both)
--max-jobs N        Max jobs to prepare per run (default: 50)
--min-score 0.6     Skip jobs below this match score (0.0–1.0)
--config PATH       Search config file (default: config/search.json)
--resumes-dir PATH  Folder with base resume PDFs (default: materials/resumes)
```

---

## Reviewing results

After a run, open the **run dashboard**:

```bash
python dashboard_run.py          # latest run
python dashboard_run.py --run 20260610_142301   # specific run
```

This shows every job prepared in that run with:
- 🔵 **LinkedIn Easy Apply** link (if available)
- 🤝 **Handshake** link (if source is Handshake)
- 🌐 **External ATS** link (Greenhouse, Lever, Workday, etc.)
- ✉️ **Gmail Draft** link — opens the draft in Gmail, ready to send
- 📄 **Resume** — opens the tailored PDF in the browser
- 📝 **Cover Letter** — opens the cover letter PDF

To track your applications over time:

```bash
python dashboard.py
```

This shows the cumulative history with a status column you can update (pending → applied → interviewing → offer / rejected) directly from the browser.

---

## Output structure

Each run creates:

```
output/
├── runs/
│   └── 20260610_142301/
│       └── manifest.json          ← all jobs in this run
└── jobs/
    └── 20260610_MockTech_Software_Engineer_Intern/
        ├── resume_software_engineer_MockTech_...pdf
        ├── CoverLetter_MockTech_...pdf
        ├── email_draft.eml        ← if Gmail API not set up
        └── job_info.json          ← all links and metadata for this job
```

---

## Swapping the LLM

The default is NVIDIA NIM (free, no credit card):

```env
# Groq (also free)
GROK_API_KEY=gsk_...
GROK_MODEL=llama-3.3-70b-versatile
# Also update GROK_BASE_URL in ai/grok_client.py to https://api.groq.com/openai/v1

# OpenAI
GROK_API_KEY=sk-...
GROK_MODEL=gpt-4o-mini
# Also update GROK_BASE_URL in ai/grok_client.py to https://api.openai.com/v1
```

---

## Project structure

```
job-hunter/
├── ai/
│   └── grok_client.py            # All LLM calls (scoring, tailoring, cover letters, email body)
├── application/
│   ├── email_draft.py            # Saves email drafts to Gmail or .eml
│   ├── browser_applicant.py      # [v1 legacy] Playwright browser automation — not called by default
│   ├── linkedin_outreach.py      # [v1 legacy] LinkedIn connection requests — not called by default
│   └── tracker.py                # Tracks processed jobs in logs/applied.json
├── resume/
│   ├── resume_builder.py         # PDF resume + cover letter renderer (ReportLab), per-job folders
│   └── resume_selector.py        # Picks best resume variant for each job
├── scrapers/
│   ├── apify_linkedin.py         # LinkedIn scraper via Apify (⚠️ untested — see note above)
│   └── apify_handshake.py        # Handshake scraper via Apify (⚠️ untested — see note above)
├── utils/
│   ├── profile.py                # Loads utils/profile.json
│   └── profile.example.json      # Template — copy to profile.json
├── materials/resumes/            # Your base resume PDFs go here
├── output/
│   ├── runs/                     # Per-run manifests
│   └── jobs/                     # Per-job output folders (resume, cover letter, email draft)
├── logs/
│   ├── applied.json              # Cumulative application history
│   └── run.log                   # Full pipeline log
├── config/
│   └── search.json               # Search terms, location, max results
├── pipeline.py                   # Main pipeline orchestration
├── main.py                       # CLI entry point
├── dashboard.py                  # Cumulative application history dashboard (port 8080)
├── dashboard_run.py              # Per-run review dashboard (port 8081)
├── .env.example
└── requirements.txt
```

---

## Known issues / roadmap

- [ ] Verify Apify field names for `easy_apply_url` and `external_url` — depends on actor version
- [ ] LinkedIn alumni/colleague contact scraping (nice to have — requires Apify actor with people search)
- [ ] Workday full automation (flows are too varied — manual finish required)
- [ ] Resume template customisation UI
- [ ] One-click "mark applied" from the run dashboard

PRs and issues welcome.

---

## Responsible use

- Respect platform ToS. Apify handles the scraping; don't abuse rate limits.
- Review the run dashboard before submitting anything. Don't apply to jobs you're not qualified for.
- The Gmail API token is stored locally and never sent anywhere.

---

## License

MIT — see [LICENSE](LICENSE)
