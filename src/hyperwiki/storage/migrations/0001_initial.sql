-- Library records imported from a Zotero-shaped source. Hyper-Wiki never writes to Zotero;
-- these tables hold its own copy.

CREATE TABLE libraries (
    id TEXT PRIMARY KEY,
    adapter TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('user', 'group')),
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    last_snapshot_id TEXT,
    UNIQUE (adapter, kind, external_id)
);

CREATE TABLE snapshots (
    library_id TEXT NOT NULL REFERENCES libraries(id),
    snapshot_id TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (library_id, snapshot_id)
);

CREATE TABLE collections (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    parent_key TEXT,
    UNIQUE (library_id, key)
);

CREATE TABLE items (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    title TEXT NOT NULL,
    trashed INTEGER NOT NULL CHECK (trashed IN (0, 1)),
    date_modified TEXT NOT NULL,
    data_json TEXT NOT NULL,
    UNIQUE (library_id, key)
);

CREATE TABLE item_collections (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, collection_id)
);
CREATE INDEX item_collections_by_collection ON item_collections (collection_id);

CREATE TABLE item_tags (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (item_id, tag)
);
CREATE INDEX item_tags_by_tag ON item_tags (tag);

CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    parent_item_id TEXT REFERENCES items(id),
    content_type TEXT NOT NULL,
    link_mode TEXT NOT NULL
        CHECK (link_mode IN ('imported_file', 'imported_url', 'linked_file', 'linked_url')),
    path TEXT,
    url TEXT,
    trashed INTEGER NOT NULL CHECK (trashed IN (0, 1)),
    date_modified TEXT NOT NULL,
    availability TEXT NOT NULL
        CHECK (availability IN ('available', 'missing', 'invalid_path', 'remote')),
    sha256 TEXT,
    size_bytes INTEGER,
    UNIQUE (library_id, key)
);
CREATE INDEX attachments_by_parent ON attachments (parent_item_id);

CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    parent_item_id TEXT REFERENCES items(id),
    html TEXT NOT NULL,
    trashed INTEGER NOT NULL CHECK (trashed IN (0, 1)),
    date_modified TEXT NOT NULL,
    UNIQUE (library_id, key)
);
CREATE INDEX notes_by_parent ON notes (parent_item_id);

CREATE TABLE annotations (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    attachment_id TEXT NOT NULL REFERENCES attachments(id),
    annotation_type TEXT NOT NULL,
    text TEXT,
    comment TEXT,
    page_label TEXT,
    color TEXT,
    date_modified TEXT NOT NULL,
    UNIQUE (library_id, key)
);
CREATE INDEX annotations_by_attachment ON annotations (attachment_id);
