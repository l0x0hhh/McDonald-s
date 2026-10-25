"""Durable quotes and at-most-once submission claims, separate from chat state."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT_ORDER_PATH = Path(__file__).resolve().parents[1] / "data" / "orders.sqlite3"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class OrderRepository:
    def __init__(self, path: Path = DEFAULT_ORDER_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS quotes (
                    quote_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    basket_hash TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    order_id TEXT UNIQUE,
                    result_json TEXT
                );
            """)

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        # FULL ensures the submission claim is on disk before the mutating call.
        db.execute("PRAGMA synchronous=FULL")
        return db

    def save_quote(self, record: dict) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO quotes(quote_id,record_json) VALUES (?,?)",
                       (record["quote_id"], _json(record)))

    def get_quote(self, quote_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT record_json FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def invalidate_quotes(self) -> None:
        """Discard unsubmitted quotes on account changes, retaining order barriers."""
        with self._connect() as db:
            db.execute("DELETE FROM quotes")

    @staticmethod
    def _order(row):
        if row is None:
            return None
        result = dict(row)
        result["record"] = json.loads(result.pop("record_json"))
        result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
        return result

    def get_by_key(self, key: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM orders WHERE idempotency_key=?", (key,)).fetchone()
        return self._order(row)

    def get_by_order_id(self, order_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return self._order(row)

    def claim_submission(self, key: str, fingerprint: str, record: dict) -> tuple[bool, dict]:
        """One transaction wins globally, including separate workers/processes."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                return False, self._order(row)
            db.execute("""INSERT INTO orders
                       (idempotency_key,fingerprint,basket_hash,quote_id,state,record_json)
                       VALUES (?,?,?,?,?,?)""",
                       (key, fingerprint, record["basket_hash"], record["quote_id"], "submitting", _json(record)))
        return True, self.get_by_key(key)

    def mark_unknown(self, key: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE orders SET state='submission_unknown' WHERE idempotency_key=? AND state='submitting'", (key,))

    def save_result(self, key: str, result: dict, verified_payment_url: str | None = None) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM orders WHERE idempotency_key=?", (key,)).fetchone()
            if row is None:
                raise KeyError("submission claim missing")
            record = json.loads(row["record_json"])
            if verified_payment_url is not None:
                # Retain the validated official URL independently of the
                # currently displayable status; unknown must not destroy it.
                record["verified_payment_url"] = verified_payment_url
            db.execute("UPDATE orders SET state=?,order_id=?,result_json=?,record_json=? WHERE idempotency_key=?",
                       (result["status"], result["order_id"], _json(result), _json(record), key))
