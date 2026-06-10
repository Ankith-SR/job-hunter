"""
utils/profile.py

Loads the candidate profile from utils/profile.json.
All personal info (name, email, phone, etc.) lives in that one file —
no hardcoded values anywhere else in the codebase.

To set up: copy utils/profile.example.json to utils/profile.json and fill it in.
"""

import json
import sys
from pathlib import Path

_PROFILE_PATH = Path(__file__).parent / "profile.json"
_EXAMPLE_PATH = Path(__file__).parent / "profile.example.json"

_REQUIRED_KEYS = [
    "first_name", "last_name", "full_name",
    "email", "phone", "phone_display",
    "linkedin", "location", "city", "university",
    "degree", "major", "grad_year",
]


def load_profile() -> dict:
    """
    Load and return the candidate profile dict.
    Exits with a helpful message if profile.json is missing or malformed.
    """
    if not _PROFILE_PATH.exists():
        print(
            f"\n❌  Profile not found: {_PROFILE_PATH}\n"
            f"\n   To fix this, copy the example and fill in your details:\n"
            f"\n       cp utils/profile.example.json utils/profile.json\n"
            f"\n   Then open utils/profile.json and replace the placeholder values.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(
            f"\n❌  utils/profile.json is not valid JSON: {e}\n"
            f"   Fix the syntax error and try again.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    # Warn about missing keys (don't exit — partial profiles are okay)
    missing = [k for k in _REQUIRED_KEYS if not profile.get(k)]
    if missing:
        print(
            f"⚠️   profile.json is missing or empty for: {', '.join(missing)}\n"
            f"   Some features may not work correctly.\n",
            file=sys.stderr,
        )

    return profile
