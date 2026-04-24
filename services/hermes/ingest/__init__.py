"""
hermes.ingest — MQTT consumer and detection workers.

Runs as a separate process from the API server so that:
    * Ingestion backpressure cannot block HTTP requests.
    * The two can be restarted independently (rare, but useful during
      a schema migration or a detection-engine hot-fix).
    * systemd can grant different privileges — ingest needs no network
      inbound, just MQTT outbound to the broker.

Entry point: `hermes.ingest.__main__:main` (also `hermes-ingest` console script).

Current state: scaffold only. Subscribes and reconnects but does no
parsing, no offsetting, and no detection. Phase 2 fills this in.
"""
