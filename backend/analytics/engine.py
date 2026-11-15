import os
from datetime import datetime, timedelta
from statistics import median
from collections import defaultdict


def analyze(sessions, events, now):
    alerts = []
    usage_by_session = defaultdict(list)
    for event in events:
        if event["kind"] == "usage":
            usage_by_session[event["session_id"]].append(event)

    def emit(key, title, detail, severity="warning"):
        alerts.append({"id": key, "title": title, "message": detail, "severity": severity})

    for session in sessions:
        sid = session["session_id"]
        percent = session.get("context_percent")
        if percent is not None:
            threshold = next((n for n in (95, 85, 70) if percent >= n), None)
            if threshold:
                emit(
                    f"context:{sid}:{threshold}",
                    f"Context above {threshold}%",
                    f"{sid}: {percent:.1f}% ({session['context_accuracy']}). Consider compaction or a fresh session.",
                )
        limit = float(os.getenv("TOKENSCOPE_SESSION_BUDGET", "10"))
        if session.get("cost_usd") is not None and session["cost_usd"] >= limit:
            emit(
                f"cost:{sid}",
                "Session cost threshold",
                f"{sid}: estimated API-equivalent cost ${session['cost_usd']:.2f}; not a subscription bill.",
            )
        if session["status"] != "completed" and now - datetime.fromisoformat(
            session["started_at"]
        ) > timedelta(hours=8):
            emit(
                f"long:{sid}",
                "Long-running session",
                f"{sid} has history spanning over eight hours. Idle time may be included.",
            )
        own = usage_by_session[sid]
        if len(own) >= 6:
            baseline = median(e["total_tokens"] for e in own[:-1])
            if baseline and own[-1]["total_tokens"] > baseline * 3:
                emit(
                    f"spike:{own[-1]['event_id']}",
                    "Token consumption spike",
                    f"Latest request used {own[-1]['total_tokens'] / baseline:.1f}× the session median; inspect request/context growth.",
                )
        models = [e["model"] for e in own if e["model"] != "unknown"]
        if len(models) > 1 and models[-1] != models[-2]:
            emit(
                f"model:{sid}:{models[-1]}",
                "Model changed",
                f"{sid}: {models[-2]} → {models[-1]}. Check whether the switch was intended.",
                "info",
            )
        if session["input_tokens"] > 10000 and session["cache_read_tokens"] == 0:
            emit(
                f"cache:{sid}",
                "No cache reads reported",
                f"{sid} reports no cache reads. Verify cache support before changing prompts.",
                "info",
            )
    for period, days in [("daily", 1), ("weekly", 7), ("monthly", 30)]:
        limit = float(os.getenv(f"TOKENSCOPE_{period.upper()}_BUDGET", "0"))
        selected = [e for e in events if datetime.fromisoformat(e["timestamp"]) >= now - timedelta(days=days)]
        cost = sum(e.get("cost_usd") or 0 for e in selected)
        if limit and cost >= limit:
            emit(
                f"budget:{period}",
                f"{period.title()} spending threshold",
                f"Rolling {days}-day estimated API-equivalent cost ${cost:.2f} exceeds ${limit:.2f}. Unpriced requests are excluded.",
            )
    return alerts
