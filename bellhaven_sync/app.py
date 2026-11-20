# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Local review app (stdlib only). A reviewer sees each proposal with its evidence and approves or rejects it.
Approving is the ONLY path that writes to the CRM."""
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .apply import StaleProposal, apply
from .store import Store

KIND_LABEL = {"fix": "Fix fields", "reparent": "Re-parent", "chow": "Change of ownership",
              "duplicate": "Duplicate", "new_account": "New account", "not_on_website": "Not on website"}
CSS = """
body{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#f5f6f8;color:#1d2330}
header{background:#1d2330;color:#fff;padding:14px 24px;display:flex;gap:24px;align-items:center}
header a{color:#cfd6e4;text-decoration:none}header a.on{color:#fff;font-weight:600}
main{max-width:1100px;margin:20px auto;padding:0 16px}
.card{background:#fff;border:1px solid #dde1e8;border-radius:10px;padding:16px 20px;margin-bottom:16px}
.kind{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;background:#e8ecf5;margin-right:6px}
.chow{background:#fde7c8}.duplicate{background:#f3dede}.new_account{background:#dcf1e1}.reparent{background:#e2e5fb}
.conf-high{color:#18794e}.conf-medium{color:#b4580a}
h3{margin:8px 0}table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
td,th{border:1px solid #e3e6ec;padding:5px 8px;text-align:left;vertical-align:top}th{background:#f7f8fa;width:180px}
.why{background:#f7f8fa;border-left:3px solid #8894aa;padding:8px 12px;font-size:14px}
.warn{background:#fff4e0;border-left:3px solid #e08a00;padding:8px 12px;font-size:14px}
pre{background:#1d2330;color:#e6e9ef;padding:10px;border-radius:6px;font-size:12px;overflow:auto}
form{display:flex;gap:8px;margin-top:10px}input[type=text]{flex:1;padding:7px;border:1px solid #ccd;border-radius:6px}
button{padding:7px 16px;border:0;border-radius:6px;cursor:pointer;font-weight:600}
.ok{background:#18794e;color:#fff}.no{background:#c0392b;color:#fff}.muted{color:#6b7385;font-size:13px}
"""


def _esc(v):
    return html.escape("" if v is None else str(v))


def _table(d):
    rows = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(json.dumps(v) if isinstance(v, (dict, list)) else v)}</td></tr>"
                   for k, v in (d or {}).items())
    return f"<table>{rows}</table>"


def _ops_text(ops):
    lines = []
    for op in ops:
        if op["op"] == "create":
            lines.append("POST /accounts  " + json.dumps(op["fields"], indent=2))
        else:
            lines.append(f"PATCH /accounts/{op['account_id']}  " + json.dumps(op["fields"], indent=2))
    return "\n".join(lines).replace("$new", "<id of the account created above>")


def render_card(p, actionable=True):
    ev = p["evidence"] or {}
    parts = [f'<div class="card"><span class="kind {p["kind"]}">{KIND_LABEL.get(p["kind"], p["kind"])}</span>'
             f'<span class="conf-{p["confidence"]}">{_esc(p["confidence"])} confidence</span>'
             f'<span class="muted"> · #{p["id"]} · {_esc(p["status"])}</span><h3>{_esc(p["title"])}</h3>',
             f'<div class="why">{_esc(ev.get("why"))}</div>']
    for w in ev.get("review_flags") or []:
        parts.append(f'<div class="warn"><b>Will be marked Needs Review:</b> {_esc(w)}</div>')
    for w in ev.get("warnings") or []:
        parts.append(f'<div class="warn">{_esc(w)}</div>')
    for key, label in (("website", "Website listing"), ("crm_account", "CRM account"),
                       ("losing_copy", "Losing copy (will be marked duplicate)"), ("surviving_copy", "Surviving copy"),
                       ("current_owner_account", "Current owner's account"), ("changes", "Changes [current, new]"),
                       ("sop_check", "Billing SOP check")):
        if ev.get(key):
            parts.append(f"<b>{label}</b>{_table(ev[key])}")
    for n in ev.get("nearest_non_matches") or []:
        parts.append(f"<b>Nearest non-match (shown for context, NOT changed)</b>{_table(n)}")
    parts.append(f"<details><summary>Exact API calls if approved</summary><pre>{_esc(_ops_text(p['ops']))}</pre></details>")
    if p.get("result"):
        parts.append(f"<details open><summary>Result</summary><pre>{_esc(json.dumps(p['result'], indent=2))}</pre></details>")
    if actionable and p["status"] == "pending":
        parts.append(f'<form method="post" action="/decide"><input type="hidden" name="id" value="{p["id"]}">'
                     f'<input type="text" name="comment" placeholder="Optional reviewer note">'
                     f'<button class="ok" name="action" value="approve">Approve &amp; apply</button>'
                     f'<button class="no" name="action" value="reject">Reject</button></form>')
    if actionable and p["status"] == "rejected":
        if p.get("reviewer_comment"):
            parts.append(f'<p class="muted">Reviewer note: {_esc(p["reviewer_comment"])}</p>')
        parts.append(f'<form method="post" action="/decide"><input type="hidden" name="id" value="{p["id"]}">'
                     f'<input type="text" name="comment" placeholder="Why reopen? (optional)">'
                     f'<button class="ok" name="action" value="reopen">Reopen for review</button></form>')
    parts.append("</div>")
    return "".join(parts)


def page(store, view):
    counts = store.counts()
    tabs = [("pending", "Review queue"), ("applied", "Applied"), ("rejected", "Rejected"), ("other", "Stale / failed")]
    nav = "".join(f'<a class="{"on" if view == k else ""}" href="/?view={k}">{label} ({counts.get(k, 0) if k != "other" else sum(counts.get(s, 0) for s in ("stale", "failed", "superseded"))})</a>'
                  for k, label in tabs)
    if view == "other":
        items = [p for s in ("stale", "failed", "superseded") for p in store.list(s)]
    else:
        items = store.list(view)
    body = "".join(render_card(p) for p in items) or '<p class="muted">Nothing here.</p>'
    return (f"<!doctype html><html><head><meta charset=utf-8><title>Bellhaven sync review</title><style>{CSS}</style>"
            f"</head><body><header><b>Bellhaven CRM sync</b>{nav}</header><main>{body}</main></body></html>")


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    crm = None

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self._send(200, page(self.store, q.get("view", ["pending"])[0]))

    def do_POST(self):
        if self.path != "/decide":
            return self._send(404, "not found")
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        pid = int(form["id"][0])
        action = form.get("action", [""])[0]
        comment = form.get("comment", [""])[0] or None
        p = self.store.get(pid)
        if p and action == "reopen":
            self.store.reopen(pid, comment)
            return self._send(303, "", headers={"Location": "/?view=rejected"})
        if not p or p["status"] != "pending":
            return self._send(303, "", headers={"Location": "/"})
        if action == "reject":
            self.store.set_status(pid, "rejected", comment)
        elif action == "approve" and self.store.claim_pending(pid):
            try:
                result = apply(self.crm, p)
                self.store.set_status(pid, "applied", comment, result)
            except StaleProposal as e:
                self.store.set_status(pid, "stale", comment, {"error": str(e)})
            except Exception as e:  # keep the queue usable; record why
                self.store.set_status(pid, "failed", comment, {"error": str(e)})
        self._send(303, "", headers={"Location": "/"})

    def log_message(self, *args):
        pass


def make_server(store, crm, port=config.REVIEW_PORT):
    Handler.store, Handler.crm = store, crm
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve():
    from .crm import CRMClient
    srv = make_server(Store(config.DB_PATH), CRMClient())
    print(f"Review app running at http://127.0.0.1:{srv.server_address[1]}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
