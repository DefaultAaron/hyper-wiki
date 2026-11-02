import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperwiki.domain.common import parse_document
from hyperwiki.domain.library import LibraryManifest
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
    assert database.execute("PRAGMA user_version").fetchone()[0] == latest_version() == 1
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
    extra = Migration(2, "extra", "CREATE TABLE extra (x INTEGER);")
    conn = connect(tmp_path / "db.sqlite3")
    try:
        assert migrate(conn, (*MIGRATIONS, extra)) == 2
        assert migrate(conn, (*MIGRATIONS, extra)) == 2
    finally:
        conn.close()


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    broken = Migration(2, "broken", "CREATE TABLE half (x INTEGER); SELECT * FROM missing;")
    conn = connect(tmp_path / "db.sqlite3")
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn, (*MIGRATIONS, broken))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
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
    assert caught.value.details == {"found": 99, "latest": 1}


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
        notes=1,
        annotations=1,
    )
