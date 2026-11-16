# Collector health

The Operations dashboard polls enrolled-device diagnostics every 15 seconds. Collectors send authenticated heartbeats at most every 15 seconds, even when no new usage appears. Device credentials may write only their own diagnostics; account cookies may read only their own devices. Raw paths, prompts and credentials are excluded by a strict schema.

A heartbeat older than 90 seconds is stale. No heartbeat means unknown. A revoked device is marked revoked. Queued events, invalid normalized records, unreadable/missing source roots or malformed source records mark the collector degraded. Counts reflect the last delivered heartbeat, not an assertion that the device is currently reachable. A missing optional CLI source may be expected on that computer.

Last successful sync is the collector-reported time of an acknowledged usage batch; it persists with invalid-record totals in the collector spool. Source parser counters cover the running scanner lifetime. Server receipt time determines staleness. Empty history is not a successful usage delivery. Offline collectors cannot report their current queue until connectivity returns.

Protocol version 1 is validated by the server; unsupported versions receive 422. Collector version is reported separately. Tool-specific parsing compatibility remains the adapter's documented format contract; no exact installed vendor CLI version is inferred. Health delivery is best effort and does not block successfully acknowledged usage. Older collectors appear unknown until upgraded. Local standalone mode has no enrolled remote devices.
