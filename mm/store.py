"""SQLite model store. Chosen over a Postgres schema/files because it needs no extra service, gives
transactions for steward edits, and the single file is easy to inspect, copy and delete."""
import json
import sqlite3
import datetime as dt
from contextlib import contextmanager

from mm import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_run(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  provider TEXT, model TEXT, status TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS item(kind TEXT, key TEXT, payload TEXT, original TEXT, confidence REAL, reasons TEXT,
  source TEXT, status TEXT, fingerprint TEXT, scan_id INTEGER, updated_at TEXT, PRIMARY KEY(kind, key));
CREATE TABLE IF NOT EXISTS evidence(key TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS llm_call(id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, ts TEXT, purpose TEXT,
  subject TEXT, attempt INTEGER, provider TEXT, model TEXT, prompt TEXT, response TEXT, input_tokens INTEGER,
  output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER, valid INTEGER, error TEXT);
"""
KEPT = ("approved", "edited", "rejected")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or config.MODEL_DB
        with self._c() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _c(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # scans and LLM calls
    def start_scan(self, provider: str, model: str) -> int:
        with self._c() as c:
            return c.execute("INSERT INTO scan_run(started_at, provider, model, status) VALUES (?,?,?, 'running')",
                             (now(), provider, model)).lastrowid

    def finish_scan(self, scan_id: int, status: str, summary: dict) -> None:
        with self._c() as c:
            c.execute("UPDATE scan_run SET finished_at=?, status=?, summary=? WHERE id=?",
                      (now(), status, json.dumps(summary), scan_id))

    def scans(self) -> list[dict]:
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT * FROM scan_run ORDER BY id DESC")]

    def log_call(self, **kw) -> int:
        cols = ["scan_id", "purpose", "subject", "attempt", "provider", "model", "prompt", "response",
                "input_tokens", "output_tokens", "cost_usd", "latency_ms"]
        with self._c() as c:
            return c.execute(f"INSERT INTO llm_call(ts, {','.join(cols)}) VALUES (?,{','.join('?' * len(cols))})",
                             (now(), *[kw.get(k) for k in cols])).lastrowid

    def mark_call(self, call_id: int, valid: bool, error: str = "") -> None:
        with self._c() as c:
            c.execute("UPDATE llm_call SET valid=?, error=? WHERE id=?", (int(valid), error[:1000], call_id))

    def calls(self, scan_id: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM llm_call", ()
        if scan_id:
            q, args = q + " WHERE scan_id=?", (scan_id,)
        with self._c() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY id", args)]

    def call_totals(self, scan_id: int | None = None) -> dict:
        rows = self.calls(scan_id)
        return {"calls": len(rows), "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 4),
                "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
                "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
                "invalid": sum(1 for r in rows if r["valid"] == 0)}

    # evidence (profile + masked samples)
    def put_evidence(self, key: str, data: dict) -> None:
        with self._c() as c:
            c.execute("INSERT OR REPLACE INTO evidence VALUES (?,?)", (key, json.dumps(data, default=str)))

    def get_evidence(self, key: str) -> dict:
        with self._c() as c:
            r = c.execute("SELECT data FROM evidence WHERE key=?", (key,)).fetchone()
        return json.loads(r["data"]) if r else {}

    # items
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        for k in ("payload", "original", "reasons"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def get(self, kind: str, key: str) -> dict | None:
        with self._c() as c:
            r = c.execute("SELECT * FROM item WHERE kind=? AND key=?", (kind, key)).fetchone()
        return self._row(r) if r else None

    def items(self, kind: str | None = None, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM item WHERE 1=1", []
        if kind:
            q, args = q + " AND kind=?", args + [kind]
        if status:
            q, args = q + " AND status=?", args + [status]
        with self._c() as c:
            return [self._row(r) for r in c.execute(q + " ORDER BY kind, key", args)]

    def upsert(self, kind, key, payload, confidence, reasons, source, fingerprint, scan_id,
               refresh_evidence=False) -> None:
        """New and suggested items are overwritten. Approved/edited/rejected items with an unchanged
        fingerprint are kept as the steward left them (only evidence is refreshed if asked)."""
        old = self.get(kind, key)
        with self._c() as c:
            if old and old["status"] in KEPT and old["fingerprint"] == fingerprint:
                if refresh_evidence:
                    merged = dict(old["payload"])
                    merged["evidence"] = payload.get("evidence")
                    c.execute("UPDATE item SET payload=?, confidence=?, reasons=?, scan_id=? WHERE kind=? AND key=?",
                              (json.dumps(merged), confidence, json.dumps(reasons), scan_id, kind, key))
                else:
                    c.execute("UPDATE item SET scan_id=? WHERE kind=? AND key=?", (scan_id, kind, key))
                return
            c.execute("INSERT OR REPLACE INTO item VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (kind, key, json.dumps(payload), None, confidence, json.dumps(reasons), source,
                       "suggested", fingerprint, scan_id, now()))

    def set_status(self, kind: str, key: str, status: str) -> None:
        with self._c() as c:
            c.execute("UPDATE item SET status=?, updated_at=? WHERE kind=? AND key=?", (status, now(), kind, key))

    def edit(self, kind: str, key: str, new_payload: dict) -> None:
        old = self.get(kind, key)
        original = old["original"] or old["payload"]
        with self._c() as c:
            c.execute("UPDATE item SET payload=?, original=?, status='edited', updated_at=? WHERE kind=? AND key=?",
                      (json.dumps(new_payload), json.dumps(original), now(), kind, key))

    def patch(self, kind: str, key: str, changes: dict) -> None:
        """Change payload fields without touching status (used by glossary merge/rename)."""
        old = self.get(kind, key)
        if old:
            with self._c() as c:
                c.execute("UPDATE item SET payload=? WHERE kind=? AND key=?",
                          (json.dumps({**old["payload"], **changes}), kind, key))

    def delete(self, kind: str, key: str) -> None:
        with self._c() as c:
            c.execute("DELETE FROM item WHERE kind=? AND key=?", (kind, key))

    def delete_unseen(self, kinds: list[str], scan_id: int) -> None:
        with self._c() as c:
            c.execute(f"DELETE FROM item WHERE kind IN ({','.join('?' * len(kinds))}) AND scan_id != ?",
                      (*kinds, scan_id))

    def bulk_approve(self, min_conf: float, kinds: list[str] | None = None) -> int:
        n = 0
        for it in self.items(status="suggested"):
            if it["confidence"] >= min_conf and (not kinds or it["kind"] in kinds) and not (it["payload"] or {}).get("error"):
                self.set_status(it["kind"], it["key"], "approved")
                n += 1
        return n
