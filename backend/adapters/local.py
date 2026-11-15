"""Read supported local session formats, extracting only usage metadata.

These are source-observed internal formats, not a promise of vendor stability.
Malformed records are isolated; incomplete JSONL tails are retried next poll.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path

from .protocol import AdapterCapabilities
from .usage import count, normalize_usage

MAX_RECORD = 8 * 1024 * 1024
FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def identity(*parts):
    return hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()


def project_name(path):
    return str(path).replace("\\", "/").rstrip("/").split("/")[-1][:128] if path else None


CAPABILITIES = [
    AdapterCapabilities(
        "claude-code",
        "~/.claude/projects/**/*.jsonl",
        FIELDS,
        "Claude Code JSONL assistant/message.usage format; fixture tested",
        "Post-response usage; latest request input is estimated context; internal format may change.",
    ),
    AdapterCapabilities(
        "codex",
        "~/.codex/sessions/**/*.jsonl",
        FIELDS,
        "Codex Rust rollout token_count/TokenUsageInfo format; fixture tested",
        "Cumulative counters converted to deltas. Context is latest request estimate, not live occupancy.",
    ),
    AdapterCapabilities(
        "gemini-cli",
        "~/.gemini/tmp/**/chats/session-*.json[l]",
        FIELDS,
        "Gemini ConversationRecord/TokensSummary JSON and JSONL; fixture tested",
        "Post-response usage; latest prompt is estimated context. Unknown patches ignored.",
    ),
]


class LocalAdapters:
    def __init__(self, claude_root=None, codex_root=None, gemini_root=None):
        self.roots = {
            "claude-code": Path(
                claude_root or os.getenv("TOKENSCOPE_CLAUDE_ROOT") or Path.home() / ".claude/projects"
            ),
            "codex": Path(
                codex_root
                or os.getenv("TOKENSCOPE_CODEX_ROOT")
                or Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
            ),
            "gemini-cli": Path(
                gemini_root or os.getenv("TOKENSCOPE_GEMINI_ROOT") or Path.home() / ".gemini/tmp"
            ),
        }
        self.states = {}
        self.errors = {tool: 0 for tool in self.roots}
        self.sessions = {}

    def capabilities(self):
        return CAPABILITIES

    def discover_sessions(self):
        return list(self.sessions.values())

    async def stream_events(self):
        while True:
            for event in self.poll():
                yield event
            await asyncio.sleep(0.2)

    def doctor(self):
        return [
            dict(
                tool=tool,
                available=root.is_dir(),
                readable=os.access(root, os.R_OK),
                source=str(root),
                malformed_records=self.errors[tool],
                authentication="filesystem only",
                limitations=cap.limitations,
                supported_versions=cap.supported_versions,
            )
            for (tool, root), cap in zip(self.roots.items(), CAPABILITIES)
        ]

    def poll(self):
        events = []
        for tool, root in self.roots.items():
            if not root.is_dir():
                continue
            patterns = ("session-*.json", "session-*.jsonl") if tool == "gemini-cli" else ("*.jsonl",)
            for pattern in patterns:
                try:
                    files = sorted(root.rglob(pattern))
                except OSError:
                    self.errors[tool] += 1
                    continue
                for path in files:
                    # Session symlinks are deliberately not followed outside roots.
                    try:
                        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                            continue
                        events.extend(self._read(tool, path))
                    except (OSError, ValueError, TypeError, KeyError, AttributeError):
                        self.errors[tool] += 1
        for event in events:
            self.sessions[event["session_id"]] = {
                key: event.get(key) for key in ("session_id", "provider", "tool", "model", "project")
            }
        return events

    def _read(self, tool, path):
        stat = path.stat()
        key = str(path)
        state = self.states.get(key)
        fingerprint = (stat.st_dev, stat.st_ino)
        if state is None or state["inode"] != fingerprint or stat.st_size < state["offset"]:
            state = dict(
                inode=fingerprint,
                offset=0,
                line=0,
                session=path.stem,
                model="unknown",
                project=None,
                previous={},
                revisions={},
            )
            self.states[key] = state
        if path.suffix == ".json":
            stamp = (stat.st_mtime_ns, stat.st_size)
            if state.get("stamp") == stamp:
                return []
            if stat.st_size > 64 * 1024 * 1024:
                raise ValueError("oversized session file")
            data = json.loads(path.read_text(encoding="utf-8"))
            state["stamp"] = stamp
            state["session"] = data.get("sessionId", path.stem)
            state["project"] = data.get("projectHash")
            return [event for item in data.get("messages", []) if (event := self._gemini(item, state))]
        result = []
        with path.open("rb") as stream:
            stream.seek(state["offset"])
            while True:
                start = stream.tell()
                raw = stream.readline(MAX_RECORD + 1)
                if not raw:
                    break
                if len(raw) > MAX_RECORD:
                    # Skip the rest of an oversized line without parsing content.
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(MAX_RECORD)
                    state["offset"] = stream.tell()
                    self.errors[tool] += 1
                    continue
                if not raw.endswith(b"\n"):
                    state["offset"] = start
                    break
                state["offset"] = stream.tell()
                state["line"] += 1
                try:
                    item = json.loads(raw)
                    if not isinstance(item, dict):
                        raise ValueError("expected object")
                    event = self._parse(tool, item, state)
                    if event:
                        result.append(event)
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.errors[tool] += 1
        return result

    def _base(self, tool, state, timestamp, request, **usage):
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError("missing timestamp")
        provider = {"claude-code": "anthropic", "codex": "openai", "gemini-cli": "google"}[tool]
        event = dict(
            event_id=identity(tool, state["session"], request),
            provider=provider,
            tool=tool,
            model=state["model"],
            session_id=f"{tool}:{state['session']}",
            request_id=str(request),
            timestamp=timestamp,
            project=state["project"],
            source=f"{tool}:local-session",
            accuracy="exact",
            context_accuracy="unavailable",
            kind="usage",
            **usage,
        )
        revision = state["revisions"].get(event["event_id"], 0) + 1
        state["revisions"][event["event_id"]] = revision
        event["revision"] = revision
        return event

    def _parse(self, tool, item, state):
        if tool == "gemini-cli":
            if "sessionId" in item:
                state["session"] = item["sessionId"]
                state["project"] = item.get("projectHash")
                return None
            if "$set" in item:
                data = item["$set"]
                state["session"] = data.get("sessionId", state["session"])
                return None
            return self._gemini(item, state)
        payload = item.get("payload") or {}
        if tool == "codex":
            if item.get("type") == "event_msg" and payload.get("type") == "context_compacted":
                event = self._base(
                    tool,
                    state,
                    item.get("timestamp"),
                    f"compaction:{state['line']}",
                    **{key: 0 for key in FIELDS},
                )
                event["kind"] = "compaction"
                return event
            if item.get("type") == "session_meta":
                state["session"] = payload.get("id", state["session"])
                state["project"] = project_name(payload.get("cwd"))
            elif item.get("type") == "turn_context":
                state["model"] = payload.get("model", state["model"])
                state["project"] = project_name(payload.get("cwd")) or state["project"]
            elif item.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                if not info or not info.get("total_token_usage"):
                    return None
                raw = info["total_token_usage"]
                # Codex can synthesize a full-context marker with total only;
                # it is not provider-reported token consumption.
                if (
                    raw.get("total_tokens", 0)
                    and not raw.get("input_tokens", 0)
                    and not raw.get("output_tokens", 0)
                ):
                    return None
                mapping = dict(
                    input_tokens="input_tokens",
                    output_tokens="output_tokens",
                    cache_read_tokens="cached_input_tokens",
                    cache_creation_tokens="cache_write_input_tokens",
                    reasoning_tokens="reasoning_output_tokens",
                    total_tokens="total_tokens",
                )
                current = {k: count(raw.get(v, 0)) for k, v in mapping.items()}
                previous = state["previous"]
                if current == previous:
                    return None
                # Stale snapshots and corrections cannot establish new consumption.
                # Keep the prior baseline until a monotonic snapshot arrives.
                if any(current[k] < previous.get(k, 0) for k in current):
                    return None
                state["previous"] = current
                usage = {k: current[k] - previous.get(k, 0) for k in current}
                event = self._base(
                    tool,
                    state,
                    item.get("timestamp"),
                    identity(item.get("timestamp"), json.dumps(current, sort_keys=True)),
                    **usage,
                    input_includes_cache=True,
                    output_includes_reasoning=True,
                )
                last = info.get("last_token_usage") or {}
                event.update(
                    context_tokens=last.get("input_tokens"),
                    context_window=info.get("model_context_window"),
                    context_accuracy="estimated",
                )
                return event
            return None
        if item.get("type") == "system" and item.get("subtype") == "compact_boundary":
            state["session"] = item.get("sessionId", state["session"])
            event = self._base(
                tool,
                state,
                item.get("timestamp"),
                f"compaction:{item.get('uuid', state['line'])}",
                **{key: 0 for key in FIELDS},
            )
            event["kind"] = "compaction"
            return event
        if item.get("type") == "assistant":
            message = item.get("message") or {}
            if not message.get("usage") or not message.get("id"):
                return None
            state["session"] = item.get("sessionId", state["session"])
            state["model"] = message.get("model", state["model"])
            state["project"] = project_name(item.get("cwd")) or state["project"]
            usage = normalize_usage("anthropic", message["usage"])
            event = self._base(tool, state, item.get("timestamp"), message["id"], **usage)
            event.update(
                context_tokens=usage["input_tokens"]
                + usage["cache_read_tokens"]
                + usage["cache_creation_tokens"],
                context_accuracy="estimated",
            )
            return event
        return None

    def _gemini(self, item, state):
        if item.get("type") != "gemini" or not item.get("tokens") or not item.get("id"):
            return None
        tokens = item["tokens"]
        state["model"] = item.get("model", state["model"])
        usage = normalize_usage(
            "google",
            dict(
                promptTokenCount=tokens.get("input", 0),
                candidatesTokenCount=tokens.get("output", 0),
                cachedContentTokenCount=tokens.get("cached", 0),
                thoughtsTokenCount=tokens.get("thoughts", 0),
                toolUsePromptTokenCount=tokens.get("tool", 0),
                totalTokenCount=tokens.get("total"),
            ),
        )
        event = self._base("gemini-cli", state, item.get("timestamp"), item["id"], **usage)
        event.update(context_tokens=usage["input_tokens"], context_accuracy="estimated")
        return event
