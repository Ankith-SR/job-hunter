#!/usr/bin/env python3
"""
scripts/create_profile.py

Interactive setup wizard — run this once to create your utils/profile.json.
Asks for your personal info and saves it locally. This file is gitignored
and never pushed to GitHub.

Usage:
    python scripts/create_profile.py
"""

import json
from pathlib import Path


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  {label}{suffix}: ").strip()
    return val if val else default


def main():
    profile_path = Path(__file__).parent.parent / "utils" / "profile.json"

    print("\n╔══════════════════════════════════════════╗")
    print("║     Job Hunter — First-Time Setup        ║")
    print("╚══════════════════════════════════════════╝")
    print(f"\nThis will create: {profile_path}")
    print("It's gitignored — your info stays on your machine.\n")

    profile = {}

    print("── Personal ─────────────────────────────────")
    profile["first_name"]    = prompt("First name")
    profile["last_name"]     = prompt("Last name")
    profile["full_name"]     = prompt("Full name", f"{profile['first_name']} {profile['last_name']}")

    print("\n── Contact ──────────────────────────────────")
    profile["email"]         = prompt("Email")
    raw_phone                = prompt("Phone (digits only, e.g. 6025551234)")
    profile["phone"]         = raw_phone
    # Auto-format as 602-555-1234 if 10 digits
    if len(raw_phone) == 10 and raw_phone.isdigit():
        profile["phone_display"] = f"{raw_phone[:3]}-{raw_phone[3:6]}-{raw_phone[6:]}"
    else:
        profile["phone_display"] = prompt("Phone display format (e.g. 602-555-1234)", raw_phone)
    profile["linkedin"]      = prompt("LinkedIn URL (e.g. https://linkedin.com/in/your-name)")

    print("\n── Location ─────────────────────────────────")
    profile["city"]          = prompt("City", "Tempe")
    profile["state_full"]    = prompt("State (full name)", "Arizona")
    profile["country"]       = prompt("Country", "United States")
    profile["zip"]           = prompt("ZIP code")
    profile["location"]      = prompt("Location string", f"{profile['city']}, {profile['state_full'][:2].upper()}")

    print("\n── Education ────────────────────────────────")
    profile["university"]    = prompt("University", "Arizona State University")
    profile["degree"]        = prompt("Degree", "M.S. Computer Science")
    profile["major"]         = prompt("Major", "Computer Science")
    profile["grad_year"]     = prompt("Graduation year", "2027")

    print("\n── Work Authorization ───────────────────────")
    profile["authorized"]    = prompt("Authorized to work in US? (Yes/No)", "Yes")
    profile["sponsorship"]   = prompt("Require sponsorship? (Yes/No)", "No")
    profile["veteran"]       = prompt("Veteran status", "I am not a protected veteran")
    profile["disability"]    = prompt("Disability status", "I do not have a disability")

    # Write file
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    print(f"\n✅ Profile saved to {profile_path}")
    print("\nNext steps:")
    print("  1. Copy .env.example to .env and fill in your API keys")
    print("  2. Add your resume PDFs to materials/resumes/")
    print("  3. python main.py --setup-session linkedin")
    print("  4. python main.py --dry-run\n")


if __name__ == "__main__":
    main()
