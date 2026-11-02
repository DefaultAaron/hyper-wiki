"""Repository for imported library records, keyed by stable internal IDs."""

import sqlite3
import uuid
from datetime import datetime
from typing import get_args

from hyperwiki.domain.common import Contract, EntityKind
from hyperwiki.domain.library import (
    Annotation,
    Attachment,
    Availability,
    Collection,
    Item,
    LibraryManifest,
    LibraryRef,
    Note,
)

LIBRARY_NAMESPACE = uuid.UUID("0b6f3c1e-8d2a-4f5b-9c7e-2a1d4e6f8b90")


def library_id_for(adapter: str, library: LibraryRef) -> str:
    return str(uuid.uuid5(LIBRARY_NAMESPACE, f"{adapter}/{library.kind}/{library.id}"))


def stable_id(library_id: str, entity: EntityKind, key: str) -> str:
    return str(uuid.uuid5(LIBRARY_NAMESPACE, f"{library_id}/{entity}/{key}"))


def _parent_item_id(library_id: str, parent_key: str | None) -> str | None:
    return None if parent_key is None else stable_id(library_id, "item", parent_key)


def upsert_library(conn: sqlite3.Connection, adapter: str, library: LibraryRef) -> str:
    library_id = library_id_for(adapter, library)
    conn.execute(
        """
        INSERT INTO libraries (id, adapter, kind, external_id, name)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET name = excluded.name
        """,
        (library_id, adapter, library.kind, library.id, library.name),
    )
    return library_id


def record_snapshot(
    conn: sqlite3.Connection, library_id: str, manifest: LibraryManifest, imported_at: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (library_id, snapshot_id, completeness, created_at, imported_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (library_id, snapshot_id) DO UPDATE SET
            completeness = excluded.completeness,
            created_at = excluded.created_at,
            imported_at = excluded.imported_at
        """,
        (
            library_id,
            manifest.snapshot_id,
            manifest.completeness,
            manifest.created_at.isoformat(),
            imported_at.isoformat(),
        ),
    )
    conn.execute(
        "UPDATE libraries SET last_snapshot_id = ? WHERE id = ?",
        (manifest.snapshot_id, library_id),
    )


def upsert_collection(conn: sqlite3.Connection, library_id: str, collection: Collection) -> None:
    conn.execute(
        """
        INSERT INTO collections (id, library_id, key, version, name, parent_key)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (library_id, key) DO UPDATE SET
            version = excluded.version,
            name = excluded.name,
            parent_key = excluded.parent_key
        """,
        (
            stable_id(library_id, "collection", collection.key),
            library_id,
            collection.key,
            collection.version,
            collection.name,
            collection.parent_key,
        ),
    )


def upsert_item(conn: sqlite3.Connection, library_id: str, item: Item) -> None:
    item_id = stable_id(library_id, "item", item.key)
    conn.execute(
        """
        INSERT INTO items
            (id, library_id, key, version, item_type, title, trashed, date_modified, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (library_id, key) DO UPDATE SET
            version = excluded.version,
            item_type = excluded.item_type,
            title = excluded.title,
            trashed = excluded.trashed,
            date_modified = excluded.date_modified,
            data_json = excluded.data_json
        """,
        (
            item_id,
            library_id,
            item.key,
            item.version,
            item.item_type,
            item.title,
            int(item.trashed),
            item.date_modified.isoformat(),
            item.model_dump_json(),
        ),
    )
    conn.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO item_tags (item_id, tag) VALUES (?, ?)",
        [(item_id, tag) for tag in item.tags],
    )
    conn.execute("DELETE FROM item_collections WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO item_collections (item_id, collection_id) VALUES (?, ?)",
        [(item_id, stable_id(library_id, "collection", key)) for key in item.collection_keys],
    )


def upsert_attachment(
    conn: sqlite3.Connection,
    library_id: str,
    attachment: Attachment,
    *,
    availability: Availability,
    sha256: str | None,
    size_bytes: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO attachments
            (id, library_id, key, version, parent_item_id, content_type, link_mode, path, url,
             trashed, date_modified, availability, sha256, size_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (library_id, key) DO UPDATE SET
            version = excluded.version,
            parent_item_id = excluded.parent_item_id,
            content_type = excluded.content_type,
            link_mode = excluded.link_mode,
            path = excluded.path,
            url = excluded.url,
            trashed = excluded.trashed,
            date_modified = excluded.date_modified,
            availability = excluded.availability,
            sha256 = excluded.sha256,
            size_bytes = excluded.size_bytes
        """,
        (
            stable_id(library_id, "attachment", attachment.key),
            library_id,
            attachment.key,
            attachment.version,
            _parent_item_id(library_id, attachment.parent_key),
            attachment.content_type,
            attachment.link_mode,
            attachment.path,
            attachment.url,
            int(attachment.trashed),
            attachment.date_modified.isoformat(),
            availability,
            sha256,
            size_bytes,
        ),
    )


def upsert_note(conn: sqlite3.Connection, library_id: str, note: Note) -> None:
    conn.execute(
        """
        INSERT INTO notes
            (id, library_id, key, version, parent_item_id, html, trashed, date_modified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (library_id, key) DO UPDATE SET
            version = excluded.version,
            parent_item_id = excluded.parent_item_id,
            html = excluded.html,
            trashed = excluded.trashed,
            date_modified = excluded.date_modified
        """,
        (
            stable_id(library_id, "note", note.key),
            library_id,
            note.key,
            note.version,
            _parent_item_id(library_id, note.parent_key),
            note.html,
            int(note.trashed),
            note.date_modified.isoformat(),
        ),
    )


def upsert_annotation(conn: sqlite3.Connection, library_id: str, annotation: Annotation) -> None:
    conn.execute(
        """
        INSERT INTO annotations
            (id, library_id, key, version, attachment_id, annotation_type, text, comment,
             page_label, color, date_modified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (library_id, key) DO UPDATE SET
            version = excluded.version,
            attachment_id = excluded.attachment_id,
            annotation_type = excluded.annotation_type,
            text = excluded.text,
            comment = excluded.comment,
            page_label = excluded.page_label,
            color = excluded.color,
            date_modified = excluded.date_modified
        """,
        (
            stable_id(library_id, "annotation", annotation.key),
            library_id,
            annotation.key,
            annotation.version,
            stable_id(library_id, "attachment", annotation.parent_key),
            annotation.annotation_type,
            annotation.text,
            annotation.comment,
            annotation.page_label,
            annotation.color,
            annotation.date_modified.isoformat(),
        ),
    )


class EntityCounts(Contract):
    collections: int
    items: int
    items_trashed: int
    attachments: int
    attachments_by_availability: dict[Availability, int]
    notes: int
    annotations: int


def _count(conn: sqlite3.Connection, sql: str, *params: object) -> int:
    value: int = conn.execute(sql, params).fetchone()[0]
    return value


def count_entities(conn: sqlite3.Connection, library_id: str) -> EntityCounts:
    by_availability: dict[Availability, int] = dict.fromkeys(get_args(Availability), 0)
    for row in conn.execute(
        "SELECT availability, COUNT(*) FROM attachments WHERE library_id = ? GROUP BY availability",
        (library_id,),
    ):
        by_availability[row[0]] = row[1]
    return EntityCounts(
        collections=_count(
            conn, "SELECT COUNT(*) FROM collections WHERE library_id = ?", library_id
        ),
        items=_count(conn, "SELECT COUNT(*) FROM items WHERE library_id = ?", library_id),
        items_trashed=_count(
            conn, "SELECT COUNT(*) FROM items WHERE library_id = ? AND trashed = 1", library_id
        ),
        attachments=sum(by_availability.values()),
        attachments_by_availability=by_availability,
        notes=_count(conn, "SELECT COUNT(*) FROM notes WHERE library_id = ?", library_id),
        annotations=_count(
            conn, "SELECT COUNT(*) FROM annotations WHERE library_id = ?", library_id
        ),
    )
