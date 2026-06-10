"""
dashboard_run.py — Per-run review dashboard.

Shows every job prepared in one pipeline run:
  - apply links (LinkedIn Easy Apply, Handshake, external ATS, email draft)
  - resume + cover letter download links
  - contacts from the same school / company

Usage:
    python dashboard_run.py                 # latest run
    python dashboard_run.py --run 20260610_142301
    python dashboard_run.py --port 8081
"""

import argparse
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

RUNS_DIR = Path("output/runs")


def latest_run_id() -> str | None:
    if not RUNS_DIR.exists():
        return None
    runs = sorted(RUNS_DIR.iterdir(), reverse=True)
    for r in runs:
        if (r / "manifest.json").exists():
            return r.name
    return None


def load_manifest(run_id: str) -> list[dict]:
    path = RUNS_DIR / run_id / "manifest.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def list_runs() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        [r.name for r in RUNS_DIR.iterdir() if (r / "manifest.json").exists()],
        reverse=True,
    )


# ── HTML rendering ─────────────────────────────────────────────────────────────

CSS = """
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#0a0d14; --surface:#111520; --border:#1e2535; --navy:#152040;
  --blue:#2563eb; --blue-glow:rgba(37,99,235,.15); --green:#10b981;
  --amber:#f59e0b; --text:#e2e8f0; --muted:#64748b;
  --hs:#a78bfa; --li:#60a5fa; --ext:#34d399;
}
body { background:var(--bg); color:var(--text); font-family:'Syne',sans-serif; min-height:100vh; }
header { background:var(--surface); border-bottom:1px solid var(--border);
  padding:18px 36px; display:flex; align-items:center; justify-content:space-between; }
.logo { font-size:20px; font-weight:800; display:flex; align-items:center; gap:10px; }
.logo-icon { width:30px;height:30px;background:var(--blue);border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-size:16px; }
.muted { font-family:'DM Mono',monospace; font-size:12px; color:var(--muted); }
.main { max-width:1280px; margin:0 auto; padding:32px 24px; }
.run-select { background:var(--navy); border:1px solid var(--border); color:var(--text);
  padding:7px 14px; border-radius:8px; font-size:13px; margin-bottom:28px; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:32px; }
.stat { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px 24px; }
.stat-label { font-size:10px; font-weight:700; letter-spacing:1.5px; text-transform:uppercase;
  color:var(--muted); margin-bottom:6px; }
.stat-value { font-size:36px; font-weight:800; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:20px; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:22px 24px; display:flex; flex-direction:column; gap:14px; }
.card-header { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }
.card-title { font-size:15px; font-weight:700; }
.card-company { font-size:13px; color:var(--muted); margin-top:2px; }
.badge { display:inline-block; padding:3px 9px; border-radius:100px;
  font-size:11px; font-weight:700; font-family:'DM Mono',monospace; }
.badge-hs  { background:rgba(167,139,250,.15); color:var(--hs); }
.badge-li  { background:rgba(96,165,250,.15);  color:var(--li); }
.badge-mock{ background:rgba(100,116,139,.15); color:var(--muted); }
.links { display:flex; flex-direction:column; gap:8px; }
.link-row { display:flex; align-items:center; gap:10px; }
.link-label { font-size:11px; color:var(--muted); width:100px; flex-shrink:0; font-family:'DM Mono',monospace; }
.link-btn { background:var(--navy); border:1px solid var(--border); color:var(--text);
  padding:5px 13px; border-radius:7px; font-size:12px; font-weight:600; text-decoration:none;
  white-space:nowrap; transition:border-color .2s; }
.link-btn:hover { border-color:var(--blue); }
.link-btn.email { border-color:var(--amber); color:var(--amber); }
.link-btn.files { border-color:var(--ext); color:var(--ext); }
.none { font-size:12px; color:var(--muted); font-family:'DM Mono',monospace; }
.contacts { margin-top:6px; }
.contacts-title { font-size:11px; font-weight:700; letter-spacing:1px; text-transform:uppercase;
  color:var(--muted); margin-bottom:6px; }
.contact-chip { display:inline-flex; align-items:center; gap:6px; background:rgba(37,99,235,.1);
  border:1px solid var(--border); border-radius:100px; padding:3px 10px;
  font-size:12px; margin:3px 3px 3px 0; }
.contact-chip a { color:var(--li); text-decoration:none; }
.contact-chip a:hover { text-decoration:underline; }
.empty { text-align:center; padding:80px 20px; color:var(--muted);
  font-family:'DM Mono',monospace; font-size:14px; }
</style>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
"""


def _link_btn(href, label, cls=""):
    if not href:
        return f'<span class="none">—</span>'
    return f'<a class="link-btn {cls}" href="{href}" target="_blank">{label}</a>'


def render_run(run_id: str, entries: list[dict]) -> str:
    total      = len(entries)
    li_count   = sum(1 for e in entries if e.get("source") == "linkedin")
    hs_count   = sum(1 for e in entries if e.get("source") == "handshake")
    email_count= sum(1 for e in entries if e.get("email_draft_link"))

    run_options = "".join(
        f'<option value="{r}" {"selected" if r == run_id else ""}>{r}</option>'
        for r in list_runs()
    )

    cards = ""
    for e in entries:
        src = e.get("source", "")
        if src == "linkedin":
            badge = '<span class="badge badge-li">LinkedIn</span>'
        elif src == "handshake":
            badge = '<span class="badge badge-hs">Handshake</span>'
        else:
            badge = f'<span class="badge badge-mock">{src}</span>'

        # Contacts block
        contacts_html = ""
        contacts = e.get("contacts") or []
        if contacts:
            chips = "".join(
                f'<span class="contact-chip"><a href="{c.get("url","#")}" target="_blank">{c.get("name","?")}</a>'
                f'<span class="muted">· {c.get("connection","")}</span></span>'
                for c in contacts
            )
            contacts_html = f'<div class="contacts"><div class="contacts-title">Alumni / Connections</div>{chips}</div>'

        # File links (relative paths served from /files/)
        resume_href = f'/files/{e["resume_path"]}' if e.get("resume_path") else None
        cover_href  = f'/files/{e["cover_letter_path"]}' if e.get("cover_letter_path") else None

        cards += f"""
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">{e.get('title','—')}</div>
              <div class="card-company">{e.get('company','—')}</div>
            </div>
            {badge}
          </div>
          <div class="links">
            <div class="link-row">
              <span class="link-label">Easy Apply</span>
              {_link_btn(e.get('easy_apply_url'), '🔵 LinkedIn Easy Apply')}
            </div>
            <div class="link-row">
              <span class="link-label">Handshake</span>
              {_link_btn(e.get('handshake_url'), '🤝 Handshake')}
            </div>
            <div class="link-row">
              <span class="link-label">External ATS</span>
              {_link_btn(e.get('external_url'), '🌐 Company Portal')}
            </div>
            <div class="link-row">
              <span class="link-label">Email Draft</span>
              {_link_btn(e.get('email_draft_link'), '✉️ Gmail Draft', 'email')}
            </div>
            <div class="link-row">
              <span class="link-label">Resume</span>
              {_link_btn(resume_href, '📄 Resume', 'files')}
            </div>
            <div class="link-row">
              <span class="link-label">Cover Letter</span>
              {_link_btn(cover_href, '📝 Cover Letter', 'files')}
            </div>
          </div>
          {contacts_html}
        </div>"""

    if not cards:
        cards = '<div class="empty">📭 No jobs in this run.</div>'

    today = datetime.now().strftime("%-d %B %Y, %I:%M %p")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Hunter — Run {run_id}</title>
{CSS}
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-icon">🎯</div>
    Job Hunter — Run Dashboard
  </div>
  <div class="muted">Updated {today}</div>
</header>
<div class="main">
  <form method="get">
    <select class="run-select" name="run" onchange="this.form.submit()">
      {run_options}
    </select>
  </form>
  <div class="stats">
    <div class="stat"><div class="stat-label">Jobs This Run</div><div class="stat-value">{total}</div></div>
    <div class="stat"><div class="stat-label">LinkedIn</div><div class="stat-value" style="color:var(--li)">{li_count}</div></div>
    <div class="stat"><div class="stat-label">Handshake</div><div class="stat-value" style="color:var(--hs)">{hs_count}</div></div>
    <div class="stat"><div class="stat-label">Email Drafts</div><div class="stat-value" style="color:var(--amber)">{email_count}</div></div>
  </div>
  <div class="cards">{cards}</div>
</div>
</body>
</html>"""


# ── HTTP server ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    run_id: str = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        # Switch run via ?run=<id>
        rid = qs.get("run", [self.run_id])[0] or self.run_id

        # Serve local PDF files
        if parsed.path.startswith("/files/"):
            self._serve_file(parsed.path[7:])
            return

        entries = load_manifest(rid)
        html    = render_run(rid, entries).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html))
        self.end_headers()
        self.wfile.write(html)

    def _serve_file(self, rel_path: str):
        p = Path(rel_path)
        if not p.exists():
            self.send_error(404)
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Job Hunter — Run Dashboard")
    parser.add_argument("--run",  default=None, help="Run ID (default: latest)")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    run_id = args.run or latest_run_id()
    if not run_id:
        print("❌ No runs found in output/runs/. Run the pipeline first.")
        return

    Handler.run_id = run_id
    print(f"🎯 Job Hunter Run Dashboard → http://localhost:{args.port}")
    print(f"   Showing run: {run_id}")
    print("   Press Ctrl+C to stop.\n")
    HTTPServer(("", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
