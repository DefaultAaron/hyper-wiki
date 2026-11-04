import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperwiki.domain.common import parse_document
from hyperwiki.domain.library import LibraryManifest, LibraryRef
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.storage.database import (
    MIGRATIONS,
    Migration,
    connect,
    latest_version,
    migrate,
    open_database,
)
from hyperwiki.storage.library_repo import (
    EntityCounts,
    count_entities,
    record_snapshot,
    stable_id,
    tombstone,
    upsert_annotation,
    upsert_attachment,
    upsert_collection,
    upsert_item,
    upsert_library,
    upsert_note,
)
from manifest_factory import NOW, minimal_manifest


def manifest() -> LibraryManifest:
    return parse_document(LibraryManifest, minimal_manifest())


def upsert_all(conn: sqlite3.Connection, data: LibraryManifest) -> str:
    library_id = upsert_library(conn, data.adapter, data.library)
    for collection in data.collections:
        upsert_collection(conn, library_id, collection)
    for item in data.items:
        upsert_item(conn, library_id, item)
    for attachment in data.attachments:
        upsert_attachment(
            conn, library_id, attachment, availability="available", sha256="a" * 64, size_bytes=3
        )
    for note in data.notes:
        upsert_note(conn, library_id, note)
    for annotation in data.annotations:
        upsert_annotation(conn, library_id, annotation)
    conn.commit()
    return library_id


def test_open_database_creates_latest_schema(database: sqlite3.Connection) -> None:
    assert database.execute("PRAGMA user_version").fetchone()[0] == latest_version() == 5
    tables = {
        row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "libraries",
        "snapshots",
        "collections",
        "items",
        "item_collections",
        "item_tags",
        "attachments",
        "notes",
        "annotations",
    } <= tables


def test_migrations_apply_in_order(tmp_path: Path) -> None:
    extra = Migration(latest_version() + 1, "extra", "CREATE TABLE extra (x INTEGER);")
    conn = connect(tmp_path / "db.sqlite3")
    try:
        assert migrate(conn, (*MIGRATIONS, extra)) == extra.version
        assert migrate(conn, (*MIGRATIONS, extra)) == extra.version
    finally:
        conn.close()


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    broken = Migration(
        latest_version() + 1, "broken", "CREATE TABLE half (x INTEGER); SELECT * FROM missing;"
    )
    conn = connect(tmp_path / "db.sqlite3")
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, (*MIGRATIONS, broken))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_version()
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert "half" not in names
    finally:
        conn.close()


def test_newer_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    conn = connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(HyperWikiError) as caught:
        open_database(path)
    assert caught.value.code is ErrorCode.UNSUPPORTED_DATABASE_VERSION
    assert caught.value.details == {"found": 99, "latest": latest_version()}


class _RacingMigrations(tuple[Migration, ...]):
    """Migrations that let a second connection migrate the same file first.

    ``migrate`` reads ``user_version`` before iterating its migrations, so migrating through
    another connection on the first iteration reproduces two processes opening a new
    database at the same moment.
    """

    path: Path
    raced = False

    def __iter__(self) -> Iterator[Migration]:
        if not self.raced:
            self.raced = True
            open_database(self.path).close()
        return super().__iter__()


def test_migration_skips_versions_applied_concurrently(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    conn = connect(path)
    try:
        racing = _RacingMigrations(MIGRATIONS)
        racing.path = path
        assert migrate(conn, racing) == latest_version()
        assert racing.raced
        names = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        assert names.count("libraries") == 1
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def _insert_job(
    conn: sqlite3.Connection,
    job_id: str,
    fingerprint: str,
    state: str,
    owner: str | None = None,
    expires: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO jobs (id, operation, subject, fingerprint, fingerprint_json, state, priority,
            forced, attempts, max_attempts, lease_owner, lease_expires_at, not_before,
            created_at, updated_at)
        VALUES (?, 'test.op', 'subject', ?, '{}', ?, 0, 0, 0, 3, ?, ?, ?, ?, ?)
        """,
        (job_id, fingerprint, state, owner, expires, NOW, NOW, NOW),
    )


def test_migration_0002_creates_job_tables(database: sqlite3.Connection) -> None:
    assert _columns(database, "attachment_versions") == [
        "id",
        "attachment_id",
        "sha256",
        "size_bytes",
        "first_seen_at",
        "last_seen_at",
    ]
    assert _columns(database, "jobs") == [
        "id",
        "operation",
        "subject",
        "fingerprint",
        "fingerprint_json",
        "state",
        "priority",
        "forced",
        "attempts",
        "max_attempts",
        "lease_owner",
        "lease_expires_at",
        "not_before",
        "last_error_json",
        "created_at",
        "updated_at",
    ]
    assert _columns(database, "completed_work") == [
        "fingerprint",
        "operation",
        "subject",
        "job_id",
        "summary_json",
        "completed_at",
    ]
    with pytest.raises(sqlite3.IntegrityError):
        _insert_job(database, "j0", "fp_a", "leased")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_job(database, "j0", "fp_a", "queued", owner="w", expires=NOW)
    _insert_job(database, "j1", "fp_a", "queued")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_job(database, "j2", "fp_a", "leased", owner="w", expires=NOW)
    _insert_job(database, "j3", "fp_b", "done")
    _insert_job(database, "j4", "fp_b", "queued")


def test_migration_0003_adds_trash_flags_and_file_mtime(database: sqlite3.Connection) -> None:
    assert "trashed" in _columns(database, "collections")
    assert "trashed" in _columns(database, "annotations")
    assert "file_mtime_ns" in _columns(database, "attachments")
    library_id = upsert_library(database, "zotero", LibraryRef(id="u", kind="user", name="L"))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO collections (id, library_id, key, version, name, trashed) "
            "VALUES ('c', ?, 'CLBADT22', 1, 'x', 2)",
            (library_id,),
        )


def test_foreign_keys_enforced(database: sqlite3.Connection) -> None:
    library_id = upsert_library(database, "snapshot", manifest().library)
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO annotations (id, library_id, key, version, attachment_id,"
            " annotation_type, date_modified) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("x", library_id, "AN2ENG22", 1, "no-such-attachment", "highlight", NOW),
        )


def test_stable_id_is_deterministic() -> None:
    assert stable_id("L", "item", "IT2ENG22") == stable_id("L", "item", "IT2ENG22")
    assert stable_id("L", "item", "IT2ENG22") != stable_id("M", "item", "IT2ENG22")


def test_upsert_item_is_idempotent(database: sqlite3.Connection) -> None:
    data = manifest()
    library_id = upsert_all(database, data)
    upsert_all(database, data)
    rows = database.execute("SELECT id FROM items").fetchall()
    assert [row["id"] for row in rows] == [stable_id(library_id, "item", "IT2ENG22")]


def test_upsert_item_replaces_tags_and_collections(database: sqlite3.Connection) -> None:
    data = manifest()
    library_id = upsert_all(database, data)
    changed = data.items[0].model_copy(update={"tags": ["b"], "collection_keys": []})
    upsert_item(database, library_id, changed)
    database.commit()
    assert [row["tag"] for row in database.execute("SELECT tag FROM item_tags")] == ["b"]
    assert database.execute("SELECT COUNT(*) FROM item_collections").fetchone()[0] == 0


def test_chinese_text_round_trips_through_database(database: sqlite3.Connection) -> None:
    raw = minimal_manifest()
    raw["items"][0]["title"] = "注意力机制综述"
    upsert_all(database, parse_document(LibraryManifest, raw))
    stored = database.execute("SELECT data_json FROM items").fetchone()["data_json"]
    assert json.loads(stored)["title"] == "注意力机制综述"


def test_record_snapshot_updates_imported_at(database: sqlite3.Connection) -> None:
    data = manifest()
    library_id = upsert_all(database, data)
    first = datetime(2026, 11, 2, tzinfo=UTC)
    second = datetime(2026, 11, 3, tzinfo=UTC)
    record_snapshot(database, library_id, data, first)
    record_snapshot(database, library_id, data, second)
    rows = database.execute("SELECT imported_at FROM snapshots").fetchall()
    assert [datetime.fromisoformat(row["imported_at"]) for row in rows] == [second]


def test_count_entities(database: sqlite3.Connection) -> None:
    library_id = upsert_all(database, manifest())
    assert count_entities(database, library_id) == EntityCounts(
        collections=1,
        items=1,
        items_trashed=0,
        attachments=1,
        attachments_by_availability={"available": 1, "missing": 0, "invalid_path": 0, "remote": 0},
        attachment_versions=0,
        notes=1,
        annotations=1,
        deleted={"collection": 0, "item": 0, "attachment": 0, "note": 0, "annotation": 0},
    )


def test_migration_0004_adds_evidence_tables(database: sqlite3.Connection) -> None:
    assert "file_path" in _columns(database, "attachments")
    assert _columns(database, "extractions") == [
        "id",
        "attachment_version_id",
        "fingerprint_json",
        "page_count",
        "created_at",
    ]
    assert _columns(database, "extraction_pages") == [
        "extraction_id",
        "page_index",
        "page_label",
        "width",
        "height",
        "text",
        "has_text",
    ]
    assert _columns(database, "evidence") == [
        "id",
        "extraction_id",
        "attachment_id",
        "page_index",
        "ordinal",
        "char_start",
        "char_end",
        "text",
        "locator_json",
    ]
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO extractions (id, attachment_version_id, fingerprint_json, page_count,"
            " created_at) VALUES ('fp_x', 'no-such-version', '{}', 0, ?)",
            (NOW,),
        )


def test_migration_0005_adds_tombstones_settings_and_exclusions(
    database: sqlite3.Connection,
) -> None:
    for table in ("collections", "items", "attachments", "notes", "annotations"):
        assert _columns(database, table)[-1] == "deleted_at"
    assert _columns(database, "workspace_settings") == ["name", "value_json"]
    assert _columns(database, "excluded_collections") == ["library_id", "collection_key"]
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO excluded_collections (library_id, collection_key) "
            "VALUES ('no-such-library', 'CL2DEEP2')"
        )


def test_tombstone_keeps_first_deletion_time(database: sqlite3.Connection) -> None:
    library_id = upsert_all(database, manifest())
    first = datetime(2026, 11, 4, tzinfo=UTC)
    assert tombstone(database, library_id, "item", "IT2ENG22", at=first)
    assert not tombstone(database, library_id, "item", "IT2ENG22", at=datetime.now(UTC))
    assert not tombstone(database, library_id, "item", "ITNONE22", at=first)
    stored = database.execute("SELECT deleted_at FROM items WHERE key = 'IT2ENG22'").fetchone()
    assert datetime.fromisoformat(stored[0]) == first
