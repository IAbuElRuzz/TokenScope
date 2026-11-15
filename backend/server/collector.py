"""Explicit outbound metadata collector with durable delivery receipts and retry spool."""

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from backend.core.schema import UsageEvent
from backend.core.transport import NoRedirect


def collector_origin(url, allow_insecure_loopback=False):
    parsed = urlsplit(url)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Invalid server port") from error
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("Server URL must be an explicit origin without credentials, path or query")
    if parsed.scheme != "https" and not (
        allow_insecure_loopback
        and parsed.scheme == "http"
        and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        raise ValueError("Remote collection requires HTTPS; only explicit loopback development may use HTTP")
    return url.rstrip("/")


def read_device_token(path):
    path = Path(path).expanduser()
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Device token file must be private (chmod 600)")
    token = path.read_text().strip()
    if not re.fullmatch(r"tsd_[A-Za-z0-9_-]{20,100}", token):
        raise ValueError("Expected a registered device credential file, not a browser/API token")
    return token


class Spool:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS deliveries (event_id TEXT,revision INTEGER,payload TEXT,delivered INTEGER DEFAULT 0,PRIMARY KEY(event_id,revision))"
        )
        self.db.commit()
        path.chmod(0o600)

    def enqueue(self, events):
        invalid = 0
        with self.db:
            for raw in events:
                try:
                    event = UsageEvent.model_validate(raw)
                except (ValueError, TypeError):
                    invalid += 1
                    continue
                self.db.execute(
                    "INSERT OR IGNORE INTO deliveries(event_id,revision,payload) VALUES(?,?,?)",
                    (event.event_id, event.revision, event.model_dump_json()),
                )
        return invalid

    def pending(self):
        return self.db.execute("SELECT COUNT(*) FROM deliveries WHERE delivered=0").fetchone()[0]

    def batch(self):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM deliveries WHERE delivered=0 ORDER BY rowid LIMIT 100"
            )
        ]

    def acknowledge(self, rows, sent):
        allowed = {(e["event_id"], e["revision"]) for e in sent}
        with self.db:
            for row in rows:
                key = (row.get("event_id"), row.get("revision"))
                if key in allowed:
                    # Receipts prevent replay on collector restarts; metadata payload is released.
                    self.db.execute(
                        "UPDATE deliveries SET delivered=1,payload=NULL WHERE event_id=? AND revision=?", key
                    )

    def close(self):
        self.db.close()


class CollectorAuthorizationError(Exception):
    pass


class Collector:
    def __init__(self, server, token, spool, scanner, allow_insecure_loopback=False):
        self.server = collector_origin(server, allow_insecure_loopback)
        self.token = token
        self.spool = spool
        self.scanner = scanner
        self.opener = build_opener(NoRedirect())

    def scan(self):
        # Pause before reading a new batch rather than drop metadata on a network outage.
        # One initial log replay can exceed this soft threshold; receipts remain durable.
        if self.spool.pending() < 100000:
            invalid = self.spool.enqueue(self.scanner.poll())
            if invalid:
                print(f"Collector skipped {invalid} invalid metadata records; inspect adapter diagnostics.")

    def flush(self):
        batch = self.spool.batch()
        if not batch:
            return 0
        request = Request(
            self.server + "/api/ingest",
            data=json.dumps({"events": batch}).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise ValueError("Oversized server acknowledgement")
                result = json.loads(raw)
        except HTTPError as error:
            if error.code in (401, 403):
                raise CollectorAuthorizationError(
                    "Device authorization rejected; stop and enroll a new credential"
                ) from None
            raise
        if not isinstance(result, dict) or not isinstance(result.get("acknowledged"), list):
            raise ValueError("Invalid server acknowledgement")
        if any(not isinstance(row, dict) for row in result["acknowledged"]):
            raise ValueError("Invalid server acknowledgement")
        previous = self.spool.pending()
        self.spool.acknowledge(result["acknowledged"], batch)
        acknowledged = previous - self.spool.pending()
        if not acknowledged:
            raise ValueError("Server acknowledged no pending metadata")
        return acknowledged

    def run(self, once=False):
        backoff = 1
        while True:
            try:
                self.scan()
                if once:
                    while self.spool.pending():
                        self.flush()
                else:
                    self.flush()
                backoff = 1
            except CollectorAuthorizationError:
                raise
            except Exception as error:
                # Error bodies, URLs and credential values are never printed.
                print(f"Collector delivery unavailable ({type(error).__name__}); metadata remains queued.")
                if once:
                    return False
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if once:
                return True
            time.sleep(0.5)


def spool_path(root, server, token):
    # Separate enrollment/origin state without persisting a secret in filenames.
    key = hashlib.sha256((server + "\0" + token).encode()).hexdigest()
    return root / "collectors" / key / "spool.db"
