"""
Job Hunter v2 — Automated job application pipeline.

Scrapes Handshake + LinkedIn via Apify, scores jobs against your resumes,
tailors resumes + cover letters with AI, saves email drafts to Gmail, and
produces a per-run review dashboard so YOU decide what to submit.
"""

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

import asyncio
import logging
import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from pipeline import JobHunterPipeline


def setup_logging():
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/run.log", encoding="utf-8"),
        ],
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Job Hunter v2 — prepare applications for review"
    )
    parser.add_argument("--config",       default="config/search.json")
    parser.add_argument("--resumes-dir",  default="materials/resumes")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Scrape & score but skip file generation")
    parser.add_argument("--boards", nargs="+",
                        choices=["handshake", "linkedin"],
                        default=["handshake", "linkedin"])
    parser.add_argument("--max-jobs",     type=int,   default=50)
    parser.add_argument("--min-score",    type=float, default=0.6)
    parser.add_argument("--mock",         action="store_true",
                        help="Use mock job data (no Apify calls)")
    return parser.parse_args()


async def main():
    args = parse_args()

    resumes_path = Path(args.resumes_dir)
    if not resumes_path.exists() or not list(resumes_path.glob("*.pdf")):
        print(f"\n❌ No resume PDFs found in '{args.resumes_dir}'")
        print("   Drop your resume PDFs there and re-run.\n")
        sys.exit(1)

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"\n❌ Config file not found: {args.config}")
        print("   Copy config/search.example.json → config/search.json and fill it in.\n")
        sys.exit(1)

    pipeline = JobHunterPipeline(args)
    await pipeline.run()


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
