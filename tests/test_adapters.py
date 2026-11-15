"""Source-shaped synthetic fixtures; never production dashboard data."""

import json

import pytest

from backend.adapters import LocalAdapters, normalize_usage

STAMP = "2026-01-01T10:00:00Z"


def scanner(tmp_path):
    roots = [tmp_path / tool for tool in ("claude", "codex", "gemini")]
    for root in roots:
        root.mkdir()
    return LocalAdapters(*roots), roots


def append(path, item, newline=True):
    with path.open("a") as stream:
        stream.write(json.dumps(item) + ("\n" if newline else ""))


def test_provider_overlap_semantics():
    assert (
        normalize_usage(
            "openai",
            dict(
                input_tokens=100,
                output_tokens=20,
                input_tokens_details={"cached_tokens": 60},
                output_tokens_details={"reasoning_tokens": 10},
            ),
        )["total_tokens"]
        == 120
    )
    assert (
        normalize_usage(
            "anthropic",
            dict(
                input_tokens=100, output_tokens=20, cache_read_input_tokens=60, cache_creation_input_tokens=30
            ),
        )["total_tokens"]
        == 210
    )
    assert (
        normalize_usage(
            "google",
            dict(
                promptTokenCount=100,
                candidatesTokenCount=20,
                thoughtsTokenCount=10,
                cachedContentTokenCount=60,
            ),
        )["total_tokens"]
        == 130
    )
    assert normalize_usage("ollama", dict(prompt_eval_count=12, eval_count=4))["total_tokens"] == 16
    with pytest.raises(ValueError):
        normalize_usage("openai", {"input_tokens": -1})


def test_claude_partial_revisions_and_privacy(tmp_path):
    monitor, roots = scanner(tmp_path)
    path = roots[0] / "session.jsonl"
    item = dict(
        type="assistant",
        sessionId="s1",
        timestamp=STAMP,
        cwd="/private/project",
        message=dict(
            id="msg-1",
            model="claude-test",
            content="TOP SECRET",
            usage=dict(input_tokens=10, output_tokens=1, cache_read_input_tokens=5),
        ),
    )
    append(path, item)
    first = monitor.poll()[0]
    assert first["total_tokens"] == 16
    assert first["context_tokens"] == 15
    item["message"]["usage"]["output_tokens"] = 3
    append(path, item)
    second = monitor.poll()[0]
    assert second["event_id"] == first["event_id"]
    assert second["revision"] == 2
    assert second["total_tokens"] == 18
    assert "TOP SECRET" not in json.dumps(second)
    assert second["project"] == "project"
    assert monitor.poll() == []
    replay = LocalAdapters(*roots).poll()
    assert [(e["event_id"], e["revision"]) for e in replay] == [
        (first["event_id"], 1),
        (first["event_id"], 2),
    ]


def test_codex_cumulative_dedup_and_context(tmp_path):
    monitor, roots = scanner(tmp_path)
    path = roots[1] / "rollout.jsonl"
    append(path, {"type": "session_meta", "payload": {"id": "s1", "cwd": "/workspace/test"}})
    append(path, {"type": "turn_context", "payload": {"model": "gpt-test"}})

    def item(total, stamp):
        return {
            "type": "event_msg",
            "timestamp": stamp,
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": total,
                        "output_tokens": 20,
                        "cached_input_tokens": 10,
                        "reasoning_output_tokens": 5,
                        "total_tokens": total + 20,
                    },
                    "last_token_usage": {"input_tokens": 80},
                    "model_context_window": 1000,
                },
            },
        }

    append(path, item(100, STAMP))
    append(path, item(100, STAMP))
    append(path, item(150, "2026-01-01T10:01:00Z"))
    events = monitor.poll()
    assert len(events) == 2
    assert sum(e["total_tokens"] for e in events) == 170
    assert events[-1]["context_tokens"] == 80
    assert events[-1]["context_accuracy"] == "estimated"
    assert LocalAdapters(*roots).poll() == events


def test_incomplete_tail_and_bad_records_recover(tmp_path):
    monitor, roots = scanner(tmp_path)
    path = roots[0] / "session.jsonl"
    path.write_text("{broken}\n")
    item = dict(
        type="assistant",
        sessionId="s",
        timestamp=STAMP,
        message=dict(id="m", model="test", usage={"input_tokens": 1}),
    )
    append(path, item, newline=False)
    assert monitor.poll() == []
    with path.open("a") as stream:
        stream.write("\n")
    assert len(monitor.poll()) == 1
    assert monitor.doctor()[0]["malformed_records"] == 1


def test_gemini_legacy_and_jsonl(tmp_path):
    monitor, roots = scanner(tmp_path)
    message = dict(
        id="g1",
        type="gemini",
        model="gemini-test",
        timestamp=STAMP,
        content="SECRET",
        tokens=dict(input=100, output=20, cached=50, thoughts=10, total=130),
    )
    path = roots[2] / "session-one.json"
    path.write_text(json.dumps(dict(sessionId="session1", projectHash="hash", messages=[message])))
    events = monitor.poll()
    assert len(events) == 1 and events[0]["total_tokens"] == 130
    assert events[0]["reasoning_tokens"] == 10
    assert monitor.poll() == []
    path2 = roots[2] / "session-two.jsonl"
    append(path2, dict(sessionId="session2", projectHash="hash"))
    append(path2, message)
    assert monitor.poll()[0]["session_id"] == "gemini-cli:session2"


def test_symlinks_not_read(tmp_path):
    monitor, roots = scanner(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n")
    (roots[0] / "link.jsonl").symlink_to(outside)
    assert monitor.poll() == []


def test_codex_out_of_order_decreases_preserve_baseline(tmp_path):
    monitor, roots = scanner(tmp_path)
    path = roots[1] / "rollout.jsonl"

    def item(total, timestamp):
        return {
            "type": "event_msg",
            "timestamp": timestamp,
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": total, "output_tokens": 0, "total_tokens": total},
                    "last_token_usage": {"input_tokens": total},
                },
            },
        }

    append(path, item(100, STAMP))
    append(path, item(50, "2026-01-01T09:00:00Z"))
    append(path, item(150, "2026-01-01T10:01:00Z"))
    events = monitor.poll()
    assert [event["total_tokens"] for event in events] == [100, 50]
    assert LocalAdapters(*roots).poll() == events


def test_configured_root_environment(tmp_path, monkeypatch):
    for name in ("CLAUDE", "CODEX", "GEMINI"):
        monkeypatch.setenv(f"TOKENSCOPE_{name}_ROOT", str(tmp_path / name))
    monitor = LocalAdapters()
    assert monitor.roots["claude-code"] == tmp_path / "CLAUDE"
    assert monitor.roots["codex"] == tmp_path / "CODEX"
    assert monitor.roots["gemini-cli"] == tmp_path / "GEMINI"
