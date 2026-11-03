-- Extracted page text and evidence spans (HW-014).

-- The adapter's resolved path of the attachment's file at the last import; extraction reads
-- it and checks the bytes against the attachment version's SHA-256.
ALTER TABLE attachments ADD COLUMN file_path TEXT;

-- One row per extraction artifact. The ID is its work fingerprint, which names the attachment
-- version, the extractor, and the settings; evidence locators refer to it.
CREATE TABLE extractions (
    id TEXT PRIMARY KEY,
    attachment_version_id TEXT NOT NULL REFERENCES attachment_versions(id),
    fingerprint_json TEXT NOT NULL,
    page_count INTEGER NOT NULL CHECK (page_count >= 0),
    created_at TEXT NOT NULL
);
CREATE INDEX extractions_by_version ON extractions (attachment_version_id);

-- Cleaned page text; evidence offsets index into it. A page without a text layer keeps its
-- (short) text and has no evidence.
CREATE TABLE extraction_pages (
    extraction_id TEXT NOT NULL REFERENCES extractions(id),
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    page_label TEXT,
    width REAL,
    height REAL,
    text TEXT NOT NULL,
    has_text INTEGER NOT NULL CHECK (has_text IN (0, 1)),
    PRIMARY KEY (extraction_id, page_index)
);

-- Evidence spans. The text is the page text from char_start to char_end; the ID is derived
-- from the locator.
CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL REFERENCES extractions(id),
    attachment_id TEXT NOT NULL REFERENCES attachments(id),
    page_index INTEGER NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    text TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    UNIQUE (extraction_id, ordinal),
    FOREIGN KEY (extraction_id, page_index)
        REFERENCES extraction_pages(extraction_id, page_index)
);
CREATE INDEX evidence_by_attachment ON evidence (attachment_id);
CREATE INDEX evidence_by_page ON evidence (extraction_id, page_index);
