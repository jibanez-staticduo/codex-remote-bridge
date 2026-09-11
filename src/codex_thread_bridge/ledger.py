"""Durable request deduplication, including interrupted and partial operations."""

import base64
import binascii
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                request_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                receipt TEXT NOT NULL
            )
        """)
        self.db.commit()

    @staticmethod
    def _fingerprint(request_id: str, method: str, params: dict):
        if not request_id or len(request_id) > 128:
            raise ValueError("request_id must contain 1–128 characters")
        return hashlib.sha256(
            json.dumps([method, params], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def lookup(self, request_id: str, method: str, params: dict, *, legacy_params=None):
        fingerprint = self._fingerprint(request_id, method, params)
        row = self.db.execute(
            "SELECT fingerprint, receipt FROM operations WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        receipt = json.loads(row[1])
        if row[0] != fingerprint:
            legacy_match = (
                receipt.get("fingerprintVersion", 1) == 1
                and legacy_params is not None
                and row[0] == self._fingerprint(request_id, method, legacy_params())
            )
            if not legacy_match:
                raise ValueError(
                    "request_id already belongs to different arguments; no action taken"
                )
        return receipt

    def begin(self, request_id: str, method: str, params: dict, *, legacy_params=None):
        fingerprint = self._fingerprint(request_id, method, params)
        receipt = {
            "requestId": request_id,
            "operation": method,
            "status": "in_progress_or_unknown",
            "startedAt": time.time(),
            "retrySafe": False,
            "fingerprintVersion": 2,
        }
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO operations VALUES (?, ?, ?)",
                (request_id, fingerprint, json.dumps(receipt)),
            ).rowcount
            existing = self.lookup(request_id, method, params, legacy_params=legacy_params)
        return bool(inserted), existing

    def save(self, receipt: dict):
        receipt = {**receipt, "updatedAt": time.time()}
        with self.db:
            self.db.execute(
                "UPDATE operations SET receipt = ? WHERE request_id = ?",
                (json.dumps(receipt), receipt["requestId"]),
            )
        return receipt

    def get(self, request_id: str):
        row = self.db.execute(
            "SELECT receipt FROM operations WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown request_id")
        return json.loads(row[0])

    def close(self):
        self.db.close()

    def list_threads(self, limit: int = 20, cursor: str | None = None):
        """Historical creation receipts only; no native thread state or prompt content."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        before = None
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or not cursor.startswith("bridge-v1:"):
                    raise ValueError
                raw = base64.b64decode(cursor[10:], altchars=b"-_", validate=True)
                text = raw.decode("ascii")
                if not text.isdecimal() or len(text) > 19:
                    raise ValueError
                before = int(text)
                if before > 9223372036854775807:
                    raise ValueError
            except (ValueError, UnicodeError, binascii.Error) as error:
                raise ValueError("Invalid bridge inventory cursor") from error
        rows = self.db.execute(
            """SELECT rowid, request_id,
                      json_extract(receipt, '$.threadId'),
                      json_extract(receipt, '$.operation'),
                      json_extract(receipt, '$.status'),
                      json_extract(receipt, '$.startedAt')
               FROM operations
               WHERE json_extract(receipt, '$.operation') IN
                     ('create_thread', 'fork_thread', 'create_worktree_thread')
                 AND json_type(receipt, '$.threadId') = 'text'
                 AND json_extract(receipt, '$.threadId') <> ''
                 AND (? IS NULL OR rowid < ?)
               ORDER BY rowid DESC LIMIT ?""",
            (before, before, limit + 1),
        ).fetchall()
        data = [
            {
                "requestId": request_id,
                "threadId": thread_id,
                "operation": operation,
                "status": status,
                "startedAt": started_at,
            }
            for _, request_id, thread_id, operation, status, started_at in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = "bridge-v1:" + base64.urlsafe_b64encode(
                str(rows[limit - 1][0]).encode("ascii")
            ).decode("ascii")
        return {
            "data": data,
            "nextCursor": next_cursor,
            "liveStateChecked": False,
            "scope": "Historical creation receipts in this endpoint's local bridge ledger. "
            "Status describes the creation operation, not current thread state; "
            "existence and archived state are unchecked. Use read_thread for live data.",
        }

    def import_legacy(self, path: Path):
        """Copy an old alias ledger without losing or silently resolving conflicting receipts."""
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            rows = source.execute(
                "SELECT request_id, fingerprint, receipt FROM operations"
            ).fetchall()
        finally:
            source.close()
        with self.db:
            for request_id, fingerprint, receipt in rows:
                existing = self.db.execute(
                    "SELECT fingerprint, receipt FROM operations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != fingerprint or json.loads(existing[1]) != json.loads(receipt):
                        raise ValueError(
                            f"Conflicting retained request {request_id!r} in legacy socket ledger "
                            f"{path}; no requests will be dispatched. Preserve both ledgers."
                        )
                else:
                    self.db.execute(
                        "INSERT INTO operations VALUES (?, ?, ?)",
                        (request_id, fingerprint, receipt),
                    )


def open_endpoint_ledger(socket_path: Path, state_dir: Path):
    """Use one ledger for a canonical endpoint, importing the supplied legacy alias if present."""
    supplied = socket_path.expanduser().absolute()
    canonical = supplied.resolve()
    state_dir = state_dir.expanduser().resolve()

    def filename(path):
        endpoint = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        return state_dir / f"operations-{endpoint}.sqlite3"

    ledger = Ledger(filename(canonical))
    legacy = filename(supplied)
    try:
        if legacy != filename(canonical) and legacy.exists():
            ledger.import_legacy(legacy)
    except BaseException:
        ledger.close()
        raise
    return canonical, ledger
