"""Synthetic test benchmark; never used by production dashboards."""

import json
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from backend.core.schema import UsageEvent
from backend.storage.store import Store


def main():
    tracemalloc.start()
    with tempfile.TemporaryDirectory() as root:
        store = Store(Path(root) / "benchmark.db")
        started = time.perf_counter()
        for i in range(2000):
            store.ingest(
                UsageEvent(
                    event_id=str(i),
                    provider="benchmark",
                    tool="synthetic-test",
                    session_id=str(i % 10),
                    timestamp=datetime.now(timezone.utc),
                    input_tokens=100,
                    output_tokens=20,
                )
            )
        duration = time.perf_counter() - started
        begin = time.perf_counter()
        store.snapshot()
        snapshot_ms = (time.perf_counter() - begin) * 1000
        print(
            json.dumps(
                {
                    "events": 2000,
                    "events_per_second": 2000 / duration,
                    "snapshot_ms": snapshot_ms,
                    "peak_memory_bytes": tracemalloc.get_traced_memory()[1],
                }
            )
        )
        store.close()


if __name__ == "__main__":
    main()
