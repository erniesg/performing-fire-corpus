CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id TEXT PRIMARY KEY,
    plan_fingerprint TEXT NOT NULL,
    plan_body TEXT NOT NULL,
    checkpoint_body TEXT NOT NULL,
    report_body TEXT,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_request_facts (
    request_fact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_sequence INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    body TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES discovery_runs(run_id),
    UNIQUE (run_id, request_sequence, attempt)
);

CREATE TABLE IF NOT EXISTS discovery_observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stable_record_id TEXT NOT NULL,
    page_sequence INTEGER NOT NULL,
    body TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES discovery_runs(run_id),
    UNIQUE (run_id, stable_record_id)
);

CREATE TABLE IF NOT EXISTS discovery_duplicate_events (
    run_id TEXT NOT NULL,
    request_fact_id TEXT NOT NULL,
    stable_record_id TEXT NOT NULL,
    occurrence_index INTEGER NOT NULL,
    committed_at TEXT NOT NULL,
    PRIMARY KEY (
        run_id, request_fact_id, stable_record_id, occurrence_index
    ),
    FOREIGN KEY (run_id) REFERENCES discovery_runs(run_id),
    FOREIGN KEY (request_fact_id)
        REFERENCES discovery_request_facts(request_fact_id)
);
