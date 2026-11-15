import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from backend.core.schema import UsageEvent
from backend.pricing.engine import Pricing
from backend.storage.history import HistoryFilters, epoch_us

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
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(events)")}
        if "occurred_us" not in columns:
            self.db.execute("ALTER TABLE events ADD COLUMN occurred_us INTEGER")
        # Resume an interrupted migration even if ALTER had already persisted.
        self.db.executemany(
            "UPDATE events SET occurred_us=? WHERE id=?",
            [
                (epoch_us(stamp), identifier)
                for identifier, stamp in self.db.execute(
                    "SELECT id,timestamp FROM events WHERE occurred_us IS NULL"
                ).fetchall()
            ],
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS events_chronological ON events(occurred_us DESC,id DESC)")
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
                "INSERT OR REPLACE INTO events(id,revision,timestamp,session_id,payload,occurred_us) VALUES(?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.revision,
                    event.timestamp.isoformat(),
                    event.session_id,
                    json.dumps(payload),
                    epoch_us(event.timestamp),
                ),
            )
        return True

    def events(self, session_id=None):
        with self.lock:
            if session_id:
                rows = self.db.execute(
                    "SELECT payload FROM events WHERE session_id=? ORDER BY occurred_us,id", (session_id,)
                ).fetchall()
            else:
                rows = self.db.execute("SELECT payload FROM events ORDER BY occurred_us,id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def filtered_events(self, filters=None):
        filters = filters or HistoryFilters()
        where, args = filters.sql()
        with self.lock:
            rows = self.db.execute(
                "SELECT payload FROM events" + where + " ORDER BY occurred_us,id", args
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def history(self, filters=None, page=1, page_size=50):
        from backend.analytics.report import build_report

        filters = filters or HistoryFilters()
        if page < 1 or not 1 <= page_size <= 200:
            raise ValueError("History pagination out of range")
        where, args = filters.sql()
        with self.lock:
            # Consistent list/count/aggregates for this response, even during concurrent ingestion.
            matched = self.filtered_events(filters)
            context_history = self.events() if filters.public() else matched
            rows = self.db.execute(
                "SELECT payload FROM events" + where + " ORDER BY occurred_us DESC,id DESC LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size],
            ).fetchall()
            options = {}
            for plural, field in [
                ("providers", "provider"),
                ("models", "model"),
                ("tools", "tool"),
                ("sessions", "session_id"),
                ("projects", "project"),
                ("devices", "device_id"),
            ]:
                expression = "session_id" if field == "session_id" else f"json_extract(payload,'$.{field}')"
                values = self.db.execute(
                    "SELECT DISTINCT " + expression + " FROM events ORDER BY 1"
                ).fetchall()
                options[plural] = [row[0] for row in values if row[0] is not None and row[0] != ""]
        report = build_report(matched, context_history=context_history)
        report["limits"][0] = (
            "All retained events matching the supplied history filters, across every result page. Context boundary checks also consider this account’s unfiltered timeline without adding its excluded usage to totals."
        )
        count = len(matched)
        return {
            "items": [json.loads(row[0]) for row in rows],
            "page": page,
            "page_size": page_size,
            "total_items": count,
            "total_pages": (count + page_size - 1) // page_size,
            "totals": report["summary"],
            "analytics": report,
            "filters": filters.public(),
            "options": options,
        }

    def request_detail(self, event_id):
        from backend.analytics.report import token_parts

        with self.lock:
            row = self.db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                return None
            event = json.loads(row[0])
            own = self.filtered_events(HistoryFilters(provider=event["provider"]))
        parts = token_parts(event)
        parts["provider_total_residual_tokens"] = (
            event["total_tokens"] - parts["input_total_tokens"] - parts["output_total_tokens"]
        )
        identities = {}
        for candidate in own:
            if candidate["kind"] == "usage" and candidate.get("request_id"):
                key = (candidate["session_id"], candidate["request_id"])
                identities.setdefault(key, []).append(candidate)
        collisions = (
            identities.get((event["session_id"], event.get("request_id")), [])
            if event.get("request_id")
            else []
        )
        collisions = [candidate for candidate in collisions if candidate["event_id"] != event_id]
        repeated = []
        if event.get("prompt_analysis_opt_in") and event.get("prompt_fingerprint") and not collisions:
            stamp = epoch_us(event["timestamp"])
            for candidate in own:
                identity = (candidate["session_id"], candidate.get("request_id") or candidate["event_id"])
                if (
                    candidate["kind"] == "usage"
                    and candidate["event_id"] != event_id
                    and candidate.get("prompt_analysis_opt_in")
                    and candidate.get("prompt_fingerprint") == event["prompt_fingerprint"]
                    and candidate.get("prompt_fingerprint_scope") == event.get("prompt_fingerprint_scope")
                    and candidate["model"] == event["model"]
                    and candidate.get("project") == event.get("project")
                    and abs(epoch_us(candidate["timestamp"]) - stamp) <= 900_000_000
                    and not (candidate.get("request_id") and len(identities.get(identity, [])) > 1)
                    and identity != (event["session_id"], event.get("request_id") or event["event_id"])
                ):
                    repeated.append(candidate)

        def evidence(records):
            return [
                {
                    key: record.get(key)
                    for key in (
                        "event_id",
                        "session_id",
                        "device_id",
                        "request_id",
                        "timestamp",
                        "total_tokens",
                        "cost_usd",
                    )
                }
                for record in records[:20]
            ]

        return {
            "event": event,
            "accounting": parts,
            "pricing": {
                "estimated_cost_usd": event.get("cost_usd"),
                "billed_cost_usd": event.get("billed_cost_usd"),
                "confidence": event.get("cost_confidence", "unavailable"),
                "source": event.get("pricing_source"),
                "version": event.get("pricing_version"),
                "record": event.get("pricing_record"),
            },
            "related": {
                "telemetry_identity_collisions": evidence(collisions),
                "repeated_prompt_candidates": evidence(repeated),
                "telemetry_identity_collision_count": len(collisions),
                "repeated_prompt_candidate_count": len(repeated),
            },
            "limits": [
                "Canonical metadata only; no raw prompts or responses are stored.",
                "Matching fingerprints concern supplied prompt text, not full context or parameters; repetition is not confirmed waste or savings.",
                "Same-request identity collisions may be duplicate telemetry rather than repeated model work. Ledger totals are not changed.",
                "Related evidence is limited to this account, provider and at most 20 records per category.",
                "Prompt candidates are within 15 minutes of the selected event; global analytics uses fixed anchored clusters.",
            ],
        }

    def delete(self, session_id=None, before=None):
        clauses, args = [], []
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if before:
            clauses.append("occurred_us<?")
            args.append(epoch_us(before))
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
                        for k in (
                            "session_id",
                            "provider",
                            "tool",
                            "model",
                            "project",
                            "billing",
                            "device_id",
                            "source_session_id",
                        )
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
        from backend.analytics.report import build_report

        analytics = build_report(events)
        return {
            "sessions": sorted(
                sessions.values(),
                key=lambda s: (-datetime.fromisoformat(s["last_seen"]).timestamp(), s["session_id"]),
            ),
            "events": events[-1000:],
            "totals": totals,
            "alerts": alerts,
            "analytics": analytics,
            "generated_at": now.isoformat(),
        }

    def retain(self, days):
        return self.delete(before=(datetime.now(timezone.utc) - timedelta(days=days)).isoformat())

    def close(self):
        self.db.close()
