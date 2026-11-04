import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hyperwiki.adapters import snapshot
from hyperwiki.adapters.snapshot import LoadedSnapshot
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.services.library_import import SyncReport
from hyperwiki.services.zotero_sync import sync_zotero
from hyperwiki.storage.library_repo import list_attachment_versions, stable_id
from zotero_db_factory import sample_library

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def location(tmp_path: Path) -> ZoteroLocation:
    data_dir = tmp_path / "Zotero"
    sample_library(data_dir).close()
    return ZoteroLocation(data_dir=data_dir, base_attachment_dir=None, profile_dir=None)


def zotero_sql(location: ZoteroLocation, sql: str, *params: Any) -> None:
    """Change the synthetic Zotero database the way Zotero itself would."""
    conn = sqlite3.connect(location.data_dir / "zotero.sqlite")
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def sync(conn: sqlite3.Connection, location: ZoteroLocation, **kwargs: Any) -> SyncReport:
    return sync_zotero(conn, location, now=lambda: NOW, **kwargs)


def events(report: SyncReport) -> list[tuple[str, str, str]]:
    return [(e.entity, e.change, e.key) for e in report.changes]


def test_first_sync_reports_every_record_as_upsert(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    report = sync(database, location)
    assert report.change_counts == {
        "collection": {"upsert": 4},
        "item": {"upsert": 4},
        "attachment": {"upsert": 4},
        "note": {"upsert": 2},
        "annotation": {"upsert": 2},
    }
    assert all(e.observed_at == NOW for e in report.changes)
    assert report.import_report.counts.items == 4
    assert report.snapshot_id == "zotero-20"
    assert report.duration_ms >= 0


def test_second_sync_without_changes_reports_nothing(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)
    report = sync(database, location)
    assert report.changes == []
    assert report.change_counts == {}


def test_edit_reports_upsert(database: sqlite3.Connection, location: ZoteroLocation) -> None:
    sync(database, location)
    zotero_sql(location, "UPDATE items SET clientVersion = 30 WHERE key = 'ITENGL22'")
    report = sync(database, location)
    assert events(report) == [("item", "upsert", "ITENGL22")]
    assert report.changes[0].version == 30


def test_trash_and_restore_are_reported_without_version_change(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    """Zotero 10 does not advance clientVersion when trashing (HW-006)."""
    sync(database, location)
    zotero_sql(
        location,
        "INSERT INTO deletedItems (itemID) SELECT itemID FROM items WHERE key = 'ITCHIN22'",
    )
    trashed = sync(database, location)
    assert events(trashed) == [
        ("item", "trash", "ITCHIN22"),
        ("attachment", "trash", "ATCHIN22"),
    ]
    zotero_sql(
        location,
        "DELETE FROM deletedItems WHERE itemID = (SELECT itemID FROM items WHERE key = 'ITCHIN22')",
    )
    restored = sync(database, location)
    assert events(restored) == [
        ("item", "restore", "ITCHIN22"),
        ("attachment", "restore", "ATCHIN22"),
    ]


def test_restore_with_edit_reports_both(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)
    zotero_sql(location, "DELETE FROM deletedItems")
    zotero_sql(location, "UPDATE items SET clientVersion = 31 WHERE key = 'ITTRSH22'")
    assert events(sync(database, location)) == [
        ("item", "restore", "ITTRSH22"),
        ("item", "upsert", "ITTRSH22"),
        ("annotation", "restore", "ANTRSH22"),
    ]


def erase(location: ZoteroLocation, *keys: str) -> None:
    """Erase notes or annotations the way emptying Zotero's trash would."""
    for key in keys:
        for table in ("itemNotes", "itemAnnotations"):
            zotero_sql(
                location,
                f"DELETE FROM {table} WHERE itemID = (SELECT itemID FROM items WHERE key = ?)",
                key,
            )
        zotero_sql(location, "DELETE FROM items WHERE key = ?", key)


def tombstoned(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (table, row[0])
        for table in ("collections", "items", "attachments", "notes", "annotations")
        for row in conn.execute(f"SELECT key FROM {table} WHERE deleted_at IS NOT NULL")
    }


def test_erased_records_are_tombstoned_once(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)
    erase(location, "NTSTND22", "ANTRSH22")
    report = sync(database, location)
    assert events(report) == [("note", "delete", "NTSTND22"), ("annotation", "delete", "ANTRSH22")]
    assert [e.version for e in report.changes] == [1, 1]
    assert tombstoned(database) == {("notes", "NTSTND22"), ("annotations", "ANTRSH22")}
    assert report.import_report.counts.deleted == {
        "collection": 0,
        "item": 0,
        "attachment": 0,
        "note": 1,
        "annotation": 1,
    }
    assert sync(database, location).changes == []


def partial(loaded: LoadedSnapshot) -> LoadedSnapshot:
    manifest = loaded.manifest.model_copy(update={"completeness": "partial"})
    return loaded.model_copy(update={"manifest": manifest})


def test_partial_read_tombstones_nothing(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)
    erase(location, "NTSTND22")

    def partial_reader(data_dir: Path, **kwargs: Any) -> LoadedSnapshot:
        return partial(read_zotero_library(data_dir, **kwargs))

    assert sync(database, location, reader=partial_reader).changes == []
    assert tombstoned(database) == set()
    assert events(sync(database, location)) == [("note", "delete", "NTSTND22")]


def test_problem_keys_are_never_reported_deleted(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    """A record Zotero still has but that cannot be read is not a deletion."""
    sync(database, location)
    zotero_sql(
        location,
        "UPDATE itemAnnotations SET type = 99 "
        "WHERE itemID = (SELECT itemID FROM items WHERE key = 'ANHIGH22')",
    )
    zotero_sql(
        location,
        "UPDATE collections SET parentCollectionID = collectionID WHERE key = 'CLCHLD22'",
    )
    report = sync(database, location)
    assert report.changes == []
    assert {(p.entity, p.key) for p in report.import_report.problems} >= {
        ("annotation", "ANHIGH22"),
        ("collection", "CLCHLD22"),
    }


def test_partial_read_reports_no_deletions(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)

    def partial_reader(data_dir: Path, **kwargs: Any) -> LoadedSnapshot:
        loaded = read_zotero_library(data_dir, **kwargs)
        manifest = loaded.manifest.model_copy(
            update={
                "completeness": "partial",
                "notes": [n for n in loaded.manifest.notes if n.key != "NTSTND22"],
            }
        )
        return loaded.model_copy(update={"manifest": manifest})

    report = sync(database, location, reader=partial_reader)
    assert report.completeness == "partial"
    assert report.changes == []


def test_failed_read_changes_nothing(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)
    before = database.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    def busy_reader(data_dir: Path, **kwargs: Any) -> LoadedSnapshot:
        raise HyperWikiError(ErrorCode.ZOTERO_DATABASE_BUSY, "busy")

    with pytest.raises(HyperWikiError) as caught:
        sync(database, location, reader=busy_reader)
    assert caught.value.code == ErrorCode.ZOTERO_DATABASE_BUSY
    assert database.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == before


def counting_hash(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    hashed: list[str] = []
    original: Callable[[Path], tuple[str, int]] = snapshot._hash_file

    def counted(path: Path) -> tuple[str, int]:
        hashed.append(path.name)
        return original(path)

    monkeypatch.setattr(snapshot, "_hash_file", counted)
    return hashed


def test_changed_file_is_rehashed_and_reported(
    database: sqlite3.Connection, location: ZoteroLocation, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PDF replaced on disk keeps its Zotero version; its bytes still count as a change."""
    first = sync(database, location)
    hashed = counting_hash(monkeypatch)
    assert sync(database, location).changes == []
    assert hashed == []  # unchanged files are not read again

    pdf = location.data_dir / "storage" / "ATENGL22" / "attention.pdf"
    stat = pdf.stat()
    pdf.write_bytes(b"%PDF-1.4 replaced outside Zotero")
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    report = sync(database, location)
    assert events(report) == [("attachment", "upsert", "ATENGL22")]
    assert report.changes[0].version == 13
    assert hashed == ["attention.pdf"]
    attachment_id = stable_id(first.library_id, "attachment", "ATENGL22")
    assert len(list_attachment_versions(database, attachment_id)) == 2


def test_missing_file_is_reported(database: sqlite3.Connection, location: ZoteroLocation) -> None:
    sync(database, location)
    (location.data_dir / "storage" / "ATCHIN22" / "中文论文.pdf").unlink()
    assert events(sync(database, location)) == [("attachment", "upsert", "ATCHIN22")]


def test_trashed_collection_is_trash_not_delete(
    database: sqlite3.Connection, location: ZoteroLocation
) -> None:
    sync(database, location)
    zotero_sql(
        location,
        "INSERT INTO deletedCollections (collectionID) "
        "SELECT collectionID FROM collections WHERE key = 'CLREAD22'",
    )
    assert events(sync(database, location)) == [
        ("collection", "trash", "CLCHLD22"),
        ("collection", "trash", "CLREAD22"),
    ]


def test_full_check_rehashes_unchanged_files(
    database: sqlite3.Connection, location: ZoteroLocation, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync(database, location)
    hashed = counting_hash(monkeypatch)
    report = sync(database, location, verify_files=True)
    assert report.changes == []
    available = report.import_report.counts.attachments_by_availability["available"]
    assert available > 0
    assert len(hashed) == available
