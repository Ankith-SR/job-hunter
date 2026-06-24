# Contributing to job-hunter

Thanks for taking a look — this started as a personal tool to stop copy-pasting resumes, and it's open source so others can use (and improve) it too. Contributions of any size are welcome, from a typo fix to a new feature.

## Ways to contribute

- **Pick up an open issue** — check the [Issues tab](../../issues), especially ones labeled [`good first issue`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) if it's your first time here.
- **Report a bug** — open an issue describing what you expected vs. what happened. Include your Python version and whether you were running `--mock`, `--dry-run`, or a full run.
- **Suggest a feature** — open an issue describing the use case. Not every idea will fit the project's scope (this is meant to stay a *review-first* prep tool, not an auto-apply bot), but all suggestions are welcome.
- **Improve docs** — README clarity, setup gotchas, typos — all fair game and genuinely useful.

## Getting set up

```bash
git clone https://github.com/YOUR_USERNAME/job-hunter.git
cd job-hunter
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
cp utils/profile.example.json utils/profile.json
```

You don't need real API keys to start exploring the code — see [Testing without API costs](#testing-without-api-costs) below.

Full setup details (Gmail API, resume folder structure, etc.) are in the [README](README.md).

## Making changes

1. Fork the repo and create a branch off `main`:
   ```bash
   git checkout -b fix/short-description
   ```
2. Make your change.
3. If you touched scraper or pipeline logic, run a mock pass to make sure nothing's broken:
   ```bash
   python main.py --mock
   ```
4. Commit with a clear message describing *what* and *why*, not just *what*:
   ```
   Fix Apify dataset_id extraction for new actor schema
   ```
5. Push and open a pull request against `main`. Reference the issue number if there is one (e.g. `Fixes #12`).

## Testing without API costs

You don't need an Apify or LLM API key to poke around:

```bash
python main.py --mock     # uses fake job data, no Apify calls
python main.py --dry-run  # scores jobs but skips file generation
```

If you're working on the Apify scrapers specifically, real actor field names matter — see open issues for what's currently unverified before assuming the fallback key lists are correct.

## Code style

Nothing enforced yet (no linter config in the repo) — just try to match the style of the file you're editing. If you want to be the one to add `ruff`/`black`/`flake8` config, that's a welcome PR on its own.

## Pull request expectations

- Keep PRs focused — one fix or feature per PR is easier to review than a grab-bag.
- Explain *why* in the PR description if the change isn't obvious from the diff alone.
- It's fine if tests don't exist yet for the part you're touching — this project doesn't have full coverage. Adding tests alongside a fix is appreciated but not required to get a PR merged.

## Questions

Open an issue, or reach out directly — contact info is in the [README](README.md). No question is too basic, especially if you're newer to open source. This repo is meant to be approachable to contribute to, not intimidating.
