-- Attachment versions, the job queue, and committed work.

-- One row per distinct content of an attachment file. Replacing a file adds a row;
-- restoring earlier bytes reuses the earlier row. attachments.sha256 marks the current one.
CREATE TABLE attachment_versions (
    id TEXT PRIMARY KEY,
    attachment_id TEXT NOT NULL REFERENCES attachments(id),
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (attachment_id, sha256)
);

-- Units of work keyed by fingerprint. Timestamps are UTC ISO-8601 with microseconds, so
-- string order is time order.
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    subject TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'leased', 'done', 'failed', 'cancelled')),
    priority INTEGER NOT NULL,
    forced INTEGER NOT NULL CHECK (forced IN (0, 1)),
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    lease_owner TEXT,
    lease_expires_at TEXT,
    not_before TEXT NOT NULL,
    last_error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((state = 'leased') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE UNIQUE INDEX jobs_one_open_per_fingerprint ON jobs (fingerprint)
    WHERE state IN ('queued', 'leased');
CREATE INDEX jobs_by_state ON jobs (state, priority, created_at);

-- The committed result of each fingerprint. A forced re-run replaces its row.
CREATE TABLE completed_work (
    fingerprint TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    subject TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    summary_json TEXT NOT NULL,
    completed_at TEXT NOT NULL
);
