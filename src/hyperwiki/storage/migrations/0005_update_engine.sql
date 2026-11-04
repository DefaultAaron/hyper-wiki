-- Update engine (HW-013): tombstones, workspace settings, and collection exclusion.

-- A record that left a complete read of its library is tombstoned: kept for history, no
-- longer counted, planned, or searched. It comes back to life if its key appears again.
ALTER TABLE collections ADD COLUMN deleted_at TEXT;
ALTER TABLE items ADD COLUMN deleted_at TEXT;
ALTER TABLE attachments ADD COLUMN deleted_at TEXT;
ALTER TABLE notes ADD COLUMN deleted_at TEXT;
ALTER TABLE annotations ADD COLUMN deleted_at TEXT;

-- Update settings and engine state (automatic, schedule, paused, last_full_check_at), one
-- JSON value per name.
CREATE TABLE workspace_settings (
    name TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

-- Collections whose items are skipped, with their subcollections. Stored by key so that an
-- exclusion survives the collection being tombstoned and coming back.
CREATE TABLE excluded_collections (
    library_id TEXT NOT NULL REFERENCES libraries(id),
    collection_key TEXT NOT NULL,
    PRIMARY KEY (library_id, collection_key)
);
