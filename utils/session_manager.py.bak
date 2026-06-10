"""
utils/session_manager.py

Manages persistent Playwright browser sessions so the bot never has to
automate login — you log in once manually and the session is reused forever.

Usage (one-time setup):
    python main.py --setup-session linkedin
    python main.py --setup-session handshake

Usage (in code):
    from utils.session_manager import SessionManager

    async with SessionManager.context("linkedin") as ctx:
        page = await ctx.new_page()
        # Already authenticated — go straight to work
"""

import asyncio
import logging
import os
import json
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext

log = logging.getLogger(__name__)

# Where session profiles are stored on disk
SESSION_DIR = Path("output/browser_sessions")

SESSION_CONFIG = {
    "linkedin": {
        "profile_dir":  SESSION_DIR / "linkedin",
        "start_url":    "https://www.linkedin.com/login",
        "check_url":    "linkedin.com/feed",
        "ready_signal": "linkedin.com/feed",   # URL fragment that means "logged in"
        "label":        "LinkedIn",
    },
    "handshake": {
        "profile_dir":  SESSION_DIR / "handshake",
        "start_url":    "https://app.joinhandshake.com/login",
        "check_url":    "app.joinhandshake.com",
        "ready_signal": "joinhandshake.com/edu",  # redirects here after login
        "label":        "Handshake",
    },
}


class SessionManager:
    """
    Static helper — no instantiation needed.
    Call SessionManager.setup(platform) for one-time manual login.
    Call SessionManager.context(platform) as an async context manager for automation.
    """

    # ── One-time manual login ─────────────────────────────────────────────────

    @staticmethod
    async def setup(platform: str) -> None:
        """
        Open a visible browser window, navigate to the login page,
        and wait for the user to log in manually. Saves the session to disk.
        The bot will reuse this session on every future run — no more logins.
        """
        cfg = _get_config(platform)
        cfg["profile_dir"].mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"  🔐  {cfg['label']} — one-time session setup")
        print(f"{'='*60}")
        print(f"\n  A browser window will open at: {cfg['start_url']}")
        print(f"  Log in normally (including 2FA if needed).")
        print(f"  Once you can see your {cfg['label']} home feed / dashboard,")
        print(f"  come back here and press Enter to save the session.\n")

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(cfg["profile_dir"]),
                headless=False,
                args=_browser_args(),
                viewport={"width": 1280, "height": 900},
                user_agent=_user_agent(),
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(cfg["start_url"], wait_until="domcontentloaded")

            # Non-blocking wait — user logs in at their own pace
            print("  Waiting for you to log in… (press Enter here once you're in)")
            await asyncio.get_event_loop().run_in_executor(None, input)

            # Verify session looks authenticated before saving
            current_url = page.url
            if cfg["ready_signal"] in current_url or cfg["check_url"] in current_url:
                print(f"\n  ✅ Logged in successfully! Session saved to:")
                print(f"     {cfg['profile_dir'].resolve()}\n")
            else:
                print(f"\n  ⚠️  Couldn't confirm login (current URL: {current_url})")
                print(f"     Session saved anyway — it may still work.\n")

            await context.close()

        print(f"  Done. Run 'python main.py --dry-run' to test.\n")

    # ── Check session health ──────────────────────────────────────────────────

    @staticmethod
    async def is_valid(platform: str) -> bool:
        """
        Quick check: open the session silently and see if we land on the
        authenticated home page without hitting a login wall.
        """
        cfg = _get_config(platform)
        if not cfg["profile_dir"].exists():
            return False

        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(cfg["profile_dir"]),
                    headless=True,
                    args=_browser_args(),
                    user_agent=_user_agent(),
                )
                page = context.pages[0] if context.pages else await context.new_page()

                # Navigate to home — if we're logged in we'll land on the feed
                home_urls = {
                    "linkedin":  "https://www.linkedin.com/feed/",
                    "handshake": "https://app.joinhandshake.com/stu/postings",
                }
                await page.goto(home_urls[platform], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                valid = cfg["ready_signal"] in page.url or cfg["check_url"] in page.url
                await context.close()
                return valid
        except Exception as e:
            log.debug(f"[SessionManager] Health check failed for {platform}: {e}")
            return False

    # ── Context manager for automation ───────────────────────────────────────

    @staticmethod
    async def get_context(platform: str, headless: bool = None) -> BrowserContext:
        """
        Return an authenticated Playwright BrowserContext.
        Caller is responsible for closing it.

        If the session doesn't exist or is expired, raises SessionExpiredError
        with a clear message telling the user to run --setup-session.
        """
        cfg = _get_config(platform)

        if not cfg["profile_dir"].exists():
            raise SessionNotFoundError(platform)

        if headless is None:
            headless = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"

        # We can't use async_playwright as a context manager here because
        # the caller needs the context to outlive this function.
        # Instead, store playwright on the context object so callers can
        # clean up properly via close_context().
        pw = await async_playwright().start()
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(cfg["profile_dir"]),
                headless=headless,
                args=_browser_args(),
                viewport={"width": 1280, "height": 900},
                user_agent=_user_agent(),
            )
            # Attach playwright handle so callers can tear down cleanly
            context._pw_handle = pw
            return context
        except Exception:
            await pw.stop()
            raise

    @staticmethod
    async def close_context(context: BrowserContext) -> None:
        """Close a context returned by get_context(), including the playwright instance."""
        try:
            await context.close()
        except Exception:
            pass
        pw = getattr(context, "_pw_handle", None)
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    # ── Convenience: verify + warn ────────────────────────────────────────────

    @staticmethod
    async def verify_and_warn(platforms: list[str]) -> dict[str, bool]:
        """
        Check all requested platforms and print a clear warning for any
        that aren't set up yet. Returns {platform: is_valid}.
        Called at pipeline startup so the user knows before jobs are scraped.
        """
        results = {}
        for platform in platforms:
            cfg = _get_config(platform)
            if not cfg["profile_dir"].exists():
                results[platform] = False
                print(
                    f"\n  ⚠️  No saved session for {cfg['label']}.\n"
                    f"     Run:  python main.py --setup-session {platform}\n"
                    f"     Then re-run the pipeline.\n"
                )
            else:
                log.info(f"[SessionManager] Session profile found for {platform} — skipping live check (use --check-sessions to verify)")
                results[platform] = True  # Optimistic — we check on actual use
        return results


# ── Exceptions ────────────────────────────────────────────────────────────────

class SessionNotFoundError(Exception):
    def __init__(self, platform: str):
        cfg = SESSION_CONFIG.get(platform, {})
        label = cfg.get("label", platform)
        super().__init__(
            f"\n\n  ❌  No saved session found for {label}.\n"
            f"      Run this first:\n\n"
            f"          python main.py --setup-session {platform}\n\n"
            f"      Then re-run the pipeline.\n"
        )


class SessionExpiredError(Exception):
    def __init__(self, platform: str):
        cfg = SESSION_CONFIG.get(platform, {})
        label = cfg.get("label", platform)
        super().__init__(
            f"\n\n  ❌  Session expired for {label}.\n"
            f"      Re-run setup:\n\n"
            f"          python main.py --setup-session {platform}\n\n"
        )


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_config(platform: str) -> dict:
    if platform not in SESSION_CONFIG:
        raise ValueError(f"Unknown platform '{platform}'. Choose from: {list(SESSION_CONFIG.keys())}")
    return SESSION_CONFIG[platform]


def _user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _browser_args() -> list[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]


# ── Diagnostic helper: export cookies/localStorage from a saved profile ──────
def export_cookies(platform: str, out_path: str) -> str:
    """
    Diagnostic helper: load cookies from the saved Playwright profile and
    write them to out_path as JSON (redact values if you prefer).
    Usage (from Python): export_cookies("handshake", "handshake_cookies.json")
    Returns the path written.
    """
    cfg = _get_config(platform)
    profile = cfg["profile_dir"]
    if not profile.exists():
        raise FileNotFoundError(f"No profile dir for {platform}: {profile}")

    async def _dump():
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,
                args=_browser_args(),
                user_agent=_user_agent(),
            )
            try:
                cookies = await context.cookies()
                # Try to read localStorage keys from the main page (best-effort)
                page = context.pages[0] if context.pages else await context.new_page()
                ls = {}
                try:
                    await page.goto(cfg["start_url"], wait_until="domcontentloaded", timeout=10000)
                    try:
                        ls = await page.evaluate(
                            "() => { let o={}; for(let i=0;i<localStorage.length;i++){ const k=localStorage.key(i); o[k]=localStorage.getItem(k);} return o; }"
                        )
                    except Exception:
                        ls = {}
                except Exception:
                    ls = {}
                return {"cookies": cookies, "localStorage": ls}
            finally:
                await context.close()

    data = asyncio.get_event_loop().run_until_complete(_dump())
    Path(out_path).write_text(json.dumps(data, indent=2))
    return out_path
