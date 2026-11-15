import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from backend.core.schema import UsageEvent
from backend.pricing.engine import Pricing

CATEGORIES = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "reasoning_tokens",
    "total_tokens",
    "tool_calls",
)


class Store:
    def __init__(self, path: Path, pricing: Pricing | None = None):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, revision INTEGER, timestamp TEXT, session_id TEXT, payload TEXT)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS tombstones (id TEXT PRIMARY KEY)")
        self.db.execute("CREATE INDEX IF NOT EXISTS events_session ON events(session_id,timestamp)")
        self.db.commit()
        self.pricing = pricing or Pricing()

    def ingest(self, event: UsageEvent) -> bool:
        if event.accounting != "delta":
            raise ValueError(
                "Only normalized request deltas are accepted; cumulative counters require adapter normalization"
            )
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM tombstones WHERE id=?", (event.event_id,)).fetchone():
                return False
            previous = self.db.execute(
                "SELECT revision,payload FROM events WHERE id=?", (event.event_id,)
            ).fetchone()
            if previous and previous[0] >= event.revision:
                return False
            payload = event.model_dump(mode="json")
            pricing = self.pricing
            if previous:
                original = json.loads(previous[1])
                if original.get("pricing_record"):
                    pricing = Pricing.__new__(Pricing)
                    pricing.catalog = {
                        "version": original["pricing_version"],
                        "models": [original["pricing_record"]],
                    }
            payload.update(pricing.quote(event))
            self.db.execute(
                "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?)",
                (
                    event.event_id,
                    event.revision,
                    event.timestamp.isoformat(),
                    event.session_id,
                    json.dumps(payload),
                ),
            )
        return True

    def events(self, session_id=None):
        with self.lock:
            if session_id:
                rows = self.db.execute(
                    "SELECT payload FROM events WHERE session_id=? ORDER BY timestamp,id", (session_id,)
                ).fetchall()
            else:
                rows = self.db.execute("SELECT payload FROM events ORDER BY timestamp,id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, session_id=None, before=None):
        clauses, args = [], []
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if before:
            clauses.append("timestamp<?")
            args.append(before)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO tombstones SELECT id FROM events" + where, args)
            count = self.db.execute("DELETE FROM events" + where, args).rowcount
        return count

    def snapshot(self):
        events = self.events()
        sessions = {}
        now = datetime.now(timezone.utc)
        for event in events:
            key = event["session_id"]
            if key not in sessions:
                sessions[key] = {
                    **{
                        k: event.get(k)
                        for k in ("session_id", "provider", "tool", "model", "project", "billing")
                    },
                    **{k: 0 for k in CATEGORIES},
                    "cost_usd": None,
                    "billed_cost_usd": None,
                    "context_tokens": None,
                    "context_window": None,
                    "context_accuracy": "unavailable",
                    "started_at": event["timestamp"],
                    "requests": 0,
                    "costed_requests": 0,
                }
            session = sessions[key]
            for category in CATEGORIES:
                session[category] += event.get(category) or 0
            for category in ("cost_usd", "billed_cost_usd"):
                if event.get(category) is not None:
                    session[category] = (session[category] or 0) + event[category]
            session["requests"] += event["kind"] == "usage"
            session["costed_requests"] += event.get("cost_usd") is not None and event["kind"] == "usage"
            if session["model"] != event["model"]:
                session.update(context_tokens=None, context_window=None, context_accuracy="unavailable")
            session.update(
                {k: event.get(k) for k in ("provider", "tool", "model", "billing", "last_kind") if k in event}
            )
            session["last_seen"] = event["timestamp"]
            session["last_kind"] = event["kind"]
            if event["kind"] in ("reset", "compaction"):
                session["context_tokens"] = None
                session["context_accuracy"] = "unavailable"
            if event.get("context_tokens") is not None:
                session.update(
                    {k: event.get(k) for k in ("context_tokens", "context_window", "context_accuracy")}
                )
            if not session.get("context_window"):
                session["context_window"] = self.pricing.context_window(event["provider"], event["model"])
        for session in sessions.values():
            age = (now - datetime.fromisoformat(session["last_seen"])).total_seconds()
            session["status"] = (
                "completed" if session["last_kind"] == "completed" else ("active" if age < 120 else "idle")
            )
            session["context_percent"] = (
                (100 * session["context_tokens"] / session["context_window"])
                if session["context_tokens"] is not None and session["context_window"]
                else None
            )
            session["remaining_context"] = (
                max(0, session["context_window"] - session["context_tokens"])
                if session["context_percent"] is not None
                else None
            )
        today = now.date().isoformat()
        daily = [event for event in events if event["timestamp"][:10] == today]
        totals = {category: sum(event.get(category) or 0 for event in daily) for category in CATEGORIES}
        priced = [event["cost_usd"] for event in daily if event.get("cost_usd") is not None]
        totals.update(
            cost_usd=sum(priced) if priced else None,
            active_sessions=sum(s["status"] == "active" for s in sessions.values()),
            unpriced_requests=sum(e["kind"] == "usage" and e.get("cost_usd") is None for e in daily),
        )
        from backend.analytics.engine import analyze

        alerts = analyze(list(sessions.values()), events, now)
        return {
            "sessions": sorted(sessions.values(), key=lambda s: (-datetime.fromisoformat(s["last_seen"]).timestamp(), s["session_id"])),
            "events": events[-1000:],
            "totals": totals,
            "alerts": alerts,
            "generated_at": now.isoformat(),
        }

    def retain(self, days):
        return self.delete(before=(datetime.now(timezone.utc) - timedelta(days=days)).isoformat())

    def close(self):
        self.db.close()
