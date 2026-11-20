# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""SQLite record of every proposal and decision. This is what makes daily re-runs safe:
a proposal whose fingerprint was already seen (pending, approved, rejected or applied) is never re-proposed."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT UNIQUE NOT NULL,
    kind TEXT, title TEXT, account_id TEXT, location_slug TEXT, confidence TEXT,
    ops TEXT, before TEXT, evidence TEXT,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | applied | rejected | failed | stale | superseded
    created_at TEXT, decided_at TEXT, reviewer_comment TEXT, result TEXT, run_id INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT, summary TEXT
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def start_run(self):
        cur = self.db.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id, summary):
        self.db.execute("UPDATE runs SET finished_at=?, summary=? WHERE id=?", (now(), json.dumps(summary), run_id))
        self.db.commit()

    def add_proposals(self, proposals, run_id):
        """Insert only unseen fingerprints. A still-pending proposal for the same account/location whose
        fingerprint changed (the data moved on) is marked superseded so the queue never shows stale items."""
        added, skipped = 0, 0
        current = {p["fingerprint"] for p in proposals}
        for p in proposals:
            exists = self.db.execute("SELECT 1 FROM proposals WHERE fingerprint=?", (p["fingerprint"],)).fetchone()
            if exists:
                skipped += 1
                continue
            self.db.execute(
                "INSERT INTO proposals (fingerprint, kind, title, account_id, location_slug, confidence, ops, before,"
                " evidence, created_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (p["fingerprint"], p["kind"], p["title"], p["account_id"], p["location_slug"], p["confidence"],
                 json.dumps(p["ops"]), json.dumps(p["before"]), json.dumps(p["evidence"]), now(), run_id))
            added += 1
        superseded = 0
        for row in self.db.execute("SELECT id, fingerprint FROM proposals WHERE status='pending'").fetchall():
            if row["fingerprint"] not in current:
                self.db.execute("UPDATE proposals SET status='superseded', decided_at=? WHERE id=?", (now(), row["id"]))
                superseded += 1
        self.db.commit()
        return {"added": added, "already_known": skipped, "superseded": superseded}

    def list(self, status=None):
        q = "SELECT * FROM proposals" + (" WHERE status=?" if status else "") + " ORDER BY id"
        rows = self.db.execute(q, (status,) if status else ()).fetchall()
        return [self._row(r) for r in rows]

    def get(self, pid):
        r = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def claim_pending(self, pid):
        """Atomically move pending -> applying so a double click can't apply twice."""
        cur = self.db.execute("UPDATE proposals SET status='applying' WHERE id=? AND status='pending'", (pid,))
        self.db.commit()
        return cur.rowcount == 1

    def reopen(self, pid, comment=None):
        """A reviewer changed their mind: rejected -> pending. Only rejected items can be reopened
        (applied changes are already in the CRM; reversing them would be a new proposal)."""
        note = f"Reopened {now()}" + (f": {comment}" if comment else "")
        cur = self.db.execute(
            "UPDATE proposals SET status='pending', decided_at=NULL, "
            "reviewer_comment=TRIM(COALESCE(reviewer_comment,'') || ' | ' || ?, ' |') "
            "WHERE id=? AND status='rejected'", (note, pid))
        self.db.commit()
        return cur.rowcount == 1

    def set_status(self, pid, status, comment=None, result=None):
        self.db.execute("UPDATE proposals SET status=?, decided_at=?, reviewer_comment=COALESCE(?, reviewer_comment),"
                        " result=? WHERE id=?", (status, now(), comment, json.dumps(result) if result else None, pid))
        self.db.commit()

    def counts(self):
        return {r[0]: r[1] for r in self.db.execute("SELECT status, COUNT(*) FROM proposals GROUP BY status")}

    @staticmethod
    def _row(r):
        d = dict(r)
        for k in ("ops", "before", "evidence", "result"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d
