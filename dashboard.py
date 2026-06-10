"""
dashboard.py — Cumulative application history tracker.

Shows every job you have marked as applied (and its current status).
Users can update status directly from the dashboard.

Usage:
    python dashboard.py
    python dashboard.py --port 8080
"""

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

TRACKER_FILE = Path("logs/applied.json")

STATUS_COLORS = {
    "pending":     "#64748b",
    "applied":     "#2563eb",
    "interviewing":"#10b981",
    "offer":       "#f59e0b",
    "rejected":    "#ef4444",
    "withdrawn":   "#6b7280",
}

CSS = """
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#0a0d14; --surface:#111520; --border:#1e2535; --navy:#152040;
  --blue:#2563eb; --blue-glow:rgba(37,99,235,.15); --green:#10b981;
  --amber:#f59e0b; --text:#e2e8f0; --muted:#64748b;
}
body { background:var(--bg); color:var(--text); font-family:'Syne',sans-serif; min-height:100vh; }
header { background:var(--surface); border-bottom:1px solid var(--border);
  padding:18px 36px; display:flex; align-items:center; justify-content:space-between; }
.logo { font-size:20px; font-weight:800; display:flex; align-items:center; gap:10px; }
.logo-icon { width:30px;height:30px;background:var(--blue);border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-size:16px; }
.muted { font-family:'DM Mono',monospace; font-size:12px; color:var(--muted); }
.main { max-width:1200px; margin:0 auto; padding:32px 24px; }
.stats { display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin-bottom:32px; }
.stat { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:18px 20px; }
.stat-label { font-size:10px; font-weight:700; letter-spacing:1.5px; text-transform:uppercase;
  color:var(--muted); margin-bottom:5px; }
.stat-value { font-size:30px; font-weight:800; }
.table-wrap { background:var(--surface); border:1px solid var(--border); border-radius:14px; overflow:hidden; }
.table-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }
.table-title { font-size:15px; font-weight:700; }
table { width:100%; border-collapse:collapse; }
thead { background:var(--navy); }
th { padding:11px 18px; text-align:left; font-size:10px; font-weight:700;
  letter-spacing:1.2px; text-transform:uppercase; color:var(--muted); }
td { padding:12px 18px; font-size:13px; border-top:1px solid var(--border); vertical-align:middle; }
tr:hover td { background:rgba(37,99,235,.04); }
td a { color:var(--text); text-decoration:none; font-weight:600; }
td a:hover { color:var(--blue); text-decoration:underline; }
.badge { display:inline-block; padding:3px 9px; border-radius:100px;
  font-size:11px; font-weight:700; font-family:'DM Mono',monospace; }
.status-badge { display:inline-block; padding:3px 10px; border-radius:100px;
  font-size:11px; font-weight:700; font-family:'DM Mono',monospace; color:#fff; }
.link-sm { font-size:12px; color:var(--blue); text-decoration:none; margin-right:10px; }
.link-sm:hover { text-decoration:underline; }
.empty { text-align:center; padding:60px 20px; color:var(--muted);
  font-family:'DM Mono',monospace; font-size:14px; }
.status-form select { background:var(--navy); border:1px solid var(--border); color:var(--text);
  padding:3px 7px; border-radius:6px; font-size:11px; cursor:pointer; }
</style>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
"""


def load_data() -> dict:
    if not TRACKER_FILE.exists():
        return {}
    try:
        return json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_data(data: dict):
    TRACKER_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def render_html(data: dict) -> str:
    jobs   = list(data.items())   # (id, info)
    total  = len(jobs)
    by_status = {}
    for _, v in jobs:
        s = v.get("status", "pending")
        by_status[s] = by_status.get(s, 0) + 1

    jobs.sort(key=lambda t: t[1].get("applied_at", ""), reverse=True)

    stat_cards = ""
    for st, color in STATUS_COLORS.items():
        stat_cards += f"""
        <div class="stat">
          <div class="stat-label">{st.title()}</div>
          <div class="stat-value" style="color:{color}">{by_status.get(st, 0)}</div>
        </div>"""

    rows = ""
    for jid, j in jobs:
        applied_at = j.get("applied_at", "")
        try:
            applied_at = datetime.fromisoformat(applied_at).strftime("%-d %b %Y %H:%M")
        except Exception:
            pass

        src = j.get("source", "")
        if src == "handshake":
            badge = '<span class="badge" style="background:rgba(167,139,250,.15);color:#a78bfa">Handshake</span>'
        elif src == "linkedin":
            badge = '<span class="badge" style="background:rgba(96,165,250,.15);color:#60a5fa">LinkedIn</span>'
        else:
            badge = f'<span class="badge" style="background:rgba(100,116,139,.15);color:#94a3b8">{src}</span>'

        status = j.get("status", "pending")
        status_color = STATUS_COLORS.get(status, "#64748b")
        status_badge = f'<span class="status-badge" style="background:{status_color}">{status}</span>'

        job_url = j.get("url") or j.get("easy_apply_url") or j.get("external_url") or "#"
        title_link = f'<a href="{job_url}" target="_blank">{j.get("title","—")}</a>'

        # Quick links
        links = ""
        if j.get("email_draft_link"):
            links += f'<a class="link-sm" href="{j["email_draft_link"]}" target="_blank">✉️ draft</a>'
        if j.get("resume_path") and Path(j["resume_path"]).exists():
            links += f'<a class="link-sm" href="/files/{j["resume_path"]}" target="_blank">📄 resume</a>'

        # Status update form
        options = "".join(
            f'<option value="{s}" {"selected" if s == status else ""}>{s}</option>'
            for s in STATUS_COLORS
        )
        status_form = f"""
        <form class="status-form" method="post" action="/update_status">
          <input type="hidden" name="id" value="{jid}">
          <select name="status" onchange="this.form.submit()">{options}</select>
        </form>"""

        rows += f"""
        <tr>
          <td>{badge}</td>
          <td>{title_link}</td>
          <td>{j.get('company','—')}</td>
          <td>{applied_at}</td>
          <td>{links}</td>
          <td>{status_form}</td>
        </tr>"""

    today = datetime.now().strftime("%-d %B %Y, %I:%M %p")
    empty_row = '<tr><td colspan="6"><div class="empty"><div>📭 No applications yet.</div></div></td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Hunter — Application Tracker</title>
{CSS}
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-icon">🎯</div>
    Job Hunter — Application Tracker
  </div>
  <div class="muted">Total: {total} &nbsp;·&nbsp; Updated {today}</div>
</header>
<div class="main">
  <div class="stats">
    <div class="stat">
      <div class="stat-label">Total</div>
      <div class="stat-value">{total}</div>
    </div>
    {stat_cards}
  </div>
  <div class="table-header">
    <div class="table-title">All Applications</div>
    <a href="/" style="background:var(--navy);border:1px solid var(--border);color:var(--text);
      padding:6px 14px;border-radius:8px;font-size:12px;font-weight:600;text-decoration:none;">↻ Refresh</a>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Source</th><th>Role</th><th>Company</th><th>Prepared</th><th>Links</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows if rows else empty_row}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/files/"):
            self._serve_file(parsed.path[7:])
            return
        html = render_html(load_data()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/update_status":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length).decode()
            params = parse_qs(body)
            jid    = (params.get("id",   [""])[0]).strip()
            status = (params.get("status",[""])[0]).strip()
            if jid and status:
                data = load_data()
                if jid in data:
                    data[jid]["status"]     = status
                    data[jid]["updated_at"] = datetime.now().isoformat()
                    save_data(data)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    print(f"🎯 Application Tracker → http://localhost:{args.port}")
    print("   Press Ctrl+C to stop.\n")
    HTTPServer(("", args.port), Handler).serve_forever()
