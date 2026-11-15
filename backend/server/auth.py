"""Single-instance account control plane; no raw passwords or credentials persist."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

COOKIE = "tokenscope_session"
SESSION_SECONDS = 12 * 60 * 60
PASSWORD_ROUNDS = 600_000


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_value(token):
    return hmac.new(token.encode("utf-8"), b"tokenscope-csrf-v1", hashlib.sha256).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("Use a password between 12 and 1024 characters")
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ROUNDS
    ).hex()
    return f"pbkdf2-sha256${PASSWORD_ROUNDS}${salt}${derived}"


def password_matches(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2-sha256" or not isinstance(password, str) or len(password) > 1024:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class AuthDB:
    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS browser_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf_hash TEXT NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL, created_at REAL NOT NULL,
                last_seen REAL, revoked_at REAL);
        """)
        self.db.commit()
        path.chmod(0o600)
        self.dummy = password_hash(secrets.token_urlsafe(32))
        self.attempts = {}

    def create_user(self, username, password, role="user"):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
            raise ValueError("Username must be 3-64 lowercase letters, numbers, dot, dash or underscore")
        if role not in ("user", "admin"):
            raise ValueError("Unknown role")
        encoded = password_hash(password)
        user_id = "u_" + secrets.token_hex(16)
        try:
            with self.lock, self.db:
                self.db.execute(
                    "INSERT INTO users VALUES(?,?,?,?,1,?)", (user_id, username, encoded, role, time.time())
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("Username already exists") from error
        return self.user(user_id)

    def user(self, user_id):
        with self.lock:
            row = self.db.execute(
                "SELECT id,username,role,enabled FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def users(self):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute("SELECT id,username,role,enabled FROM users ORDER BY username")
            ]

    def set_enabled(self, username, enabled):
        with self.lock, self.db:
            row = self.db.execute("SELECT id FROM users WHERE username=?", (username.lower(),)).fetchone()
            if not row:
                raise ValueError("Unknown user")
            self.db.execute("UPDATE users SET enabled=? WHERE id=?", (int(enabled), row["id"]))
            if not enabled:
                self.db.execute("DELETE FROM browser_sessions WHERE user_id=?", (row["id"],))
                self.db.execute(
                    "UPDATE devices SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (time.time(), row["id"]),
                )

    def login(self, username, password, client):
        now = time.time()
        # Bounded in-memory throttling; restart clears it, so use proxy rate limiting too.
        with self.lock:
            self.attempts = {
                key: times for key, times in self.attempts.items() if times and times[-1] > now - 900
            }
            keys = (digest("ip:" + client), digest("user:" + username.lower()))
            for key, maximum in zip(keys, (30, 10)):
                times = [value for value in self.attempts.get(key, []) if value > now - 900]
                if len(times) >= maximum or len(self.attempts) > 10000:
                    raise PermissionError("Login temporarily rate limited")
                self.attempts[key] = times + [now]
            row = self.db.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
        valid = password_matches(password, row["password_hash"] if row else self.dummy)
        if not valid or not row or not row["enabled"]:
            return None
        token = secrets.token_urlsafe(32)
        csrf = csrf_value(token)
        with self.lock, self.db:
            self.db.execute("DELETE FROM browser_sessions WHERE expires_at<?", (now,))
            self.db.execute(
                "INSERT INTO browser_sessions VALUES(?,?,?,?)",
                (digest(token), row["id"], digest(csrf), now + SESSION_SECONDS),
            )
        return token, csrf, self.user(row["id"])

    def session(self, token):
        if not token:
            return None
        with self.lock:
            row = self.db.execute(
                """SELECT s.*,u.username,u.role,u.enabled FROM browser_sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1""",
                (digest(token), time.time()),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM browser_sessions WHERE token_hash=?", (digest(token),))

    def devices(self, user_id):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,name,created_at,last_seen,revoked_at FROM devices WHERE user_id=? ORDER BY created_at DESC",
                    (user_id,),
                )
            ]

    def create_device(self, user_id, name):
        device_id = "d_" + secrets.token_hex(16)
        token = "tsd_" + secrets.token_urlsafe(40)
        now = time.time()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,?,NULL,NULL)",
                (device_id, user_id, name, digest(token), now),
            )
        return dict(id=device_id, name=name, created_at=now, last_seen=None, revoked_at=None), token

    def device(self, token):
        if not token:
            return None
        with self.lock, self.db:
            row = self.db.execute(
                """SELECT d.id,d.user_id FROM devices d JOIN users u ON u.id=d.user_id
                WHERE d.token_hash=? AND d.revoked_at IS NULL AND u.enabled=1""",
                (digest(token),),
            ).fetchone()
            if row:
                self.db.execute("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), row["id"]))
        return dict(row) if row else None

    def revoke(self, user_id, device_id):
        with self.lock, self.db:
            return bool(
                self.db.execute(
                    "UPDATE devices SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                    (time.time(), device_id, user_id),
                ).rowcount
            )

    def set_password(self, username, password):
        encoded = password_hash(password)
        with self.lock, self.db:
            row = self.db.execute("SELECT id FROM users WHERE username=?", (username.lower(),)).fetchone()
            if not row:
                raise ValueError("Unknown user")
            self.db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, row["id"]))
            self.db.execute("DELETE FROM browser_sessions WHERE user_id=?", (row["id"],))
            self.db.execute(
                "UPDATE devices SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (time.time(), row["id"]),
            )

    def close(self):
        self.db.close()
