-- Live Zotero adapter (HW-011, ADR 0004). Trashed collections and annotations are kept with a
-- flag instead of disappearing from a read, so absence can mean deletion. The attachment
-- file's modification time lets an unchanged file skip rehashing.

ALTER TABLE collections ADD COLUMN trashed INTEGER NOT NULL DEFAULT 0 CHECK (trashed IN (0, 1));
ALTER TABLE annotations ADD COLUMN trashed INTEGER NOT NULL DEFAULT 0 CHECK (trashed IN (0, 1));
ALTER TABLE attachments ADD COLUMN file_mtime_ns INTEGER;
