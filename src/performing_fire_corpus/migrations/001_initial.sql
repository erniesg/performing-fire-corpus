PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS records (
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (record_type, record_id)
);

CREATE TABLE IF NOT EXISTS asset_states (
    asset_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    resume_state TEXT,
    blocker TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    required_capabilities TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    active_lease_id TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (asset_id, operation)
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT,
    checkpoint TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_lease_per_job
    ON leases(job_id) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
    subject_id TEXT NOT NULL,
    link_kind TEXT NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (subject_id, link_kind, url)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
