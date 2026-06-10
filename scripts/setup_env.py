#!/usr/bin/env python3
"""
scripts/setup_env.py

Interactive setup to create a local .env file.
Do NOT commit .env to git — it contains your API keys.

Usage:
    python scripts/setup_env.py
"""

import os
from pathlib import Path


def prompt(key: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  {key}{suffix}: ").strip()
    return val if val else default


def main():
    env_path = Path(__file__).parent.parent / ".env"

    print("\n╔══════════════════════════════════════════╗")
    print("║     Job Hunter — Environment Setup       ║")
    print("╚══════════════════════════════════════════╝")
    print(f"\nThis will create: {env_path}")
    print("It's gitignored — your keys stay on your machine.\n")

    if env_path.exists():
        overwrite = input("  .env already exists — overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            print("  Aborted. .env not changed.\n")
            return

    env = {}

    print("── LLM API ──────────────────────────────────")
    print("  Default: NVIDIA NIM (free). Get a key at https://integrate.api.nvidia.com")
    env["GROK_API_KEY"]       = prompt("NVIDIA / LLM API key", "")
    env["GROK_MODEL"]         = prompt("Model (leave blank for default)", "meta/llama-3.3-70b-instruct")

    print("\n── Apify (job scraping) ─────────────────────")
    print("  Get a token at https://console.apify.com → Settings → API")
    env["APIFY_TOKEN"]        = prompt("Apify API token", "")
    env["APIFY_ACTOR_ID"]     = prompt("Apify actor ID for Handshake", "orgupdate/handshake-jobs-scraper")
    env["APIFY_MAX_ITEMS"]    = prompt("Max items per Apify run", "50")
    env["APIFY_USE_PROXY"]    = prompt("Use Apify proxy? (true/false)", "true")

    print("\n── Email (outreach fallback) ─────────────────")
    print("  Use a Gmail App Password, not your real password.")
    print("  https://support.google.com/accounts/answer/185833")
    env["EMAIL_ADDRESS"]      = prompt("Gmail address", "")
    env["EMAIL_APP_PASSWORD"] = prompt("Gmail App Password", "")

    print("\n── Browser ──────────────────────────────────")
    env["BROWSER_HEADLESS"]   = prompt("Run browser headless? (true/false)", "true")

    # Write file
    lines = []
    section = None
    for k, v in env.items():
        lines.append(f"{k}={v}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n✅ Written to {env_path}")
    print("\nNext steps:")
    print("  1. python scripts/create_profile.py   ← set your personal info")
    print("  2. Add resume PDFs to materials/resumes/")
    print("  3. python main.py --setup-session linkedin")
    print("  4. python main.py --mock --dry-run    ← test without scraping\n")


if __name__ == "__main__":
    main()
