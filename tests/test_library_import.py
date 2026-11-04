import shutil
import sqlite3
from pathlib import Path

import pytest

from hyperwiki.errors import ErrorCode
from hyperwiki.services.library_import import import_snapshot
from hyperwiki.services.status import library_status
from snapshot_mutations import (
    delete_item,
    edit_item_title,
    load_manifest_dict,
    make_partial,
    set_trashed,
    write_manifest_dict,
)

NO_DELETIONS = {"collection": 0, "item": 0, "attachment": 0, "note": 0, "annotation": 0}


def item_ids(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row["key"], row["id"]) for row in conn.execute("SELECT key, id FROM items ORDER BY key")
    ]


def tombstoned(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (table, row[0])
        for table in ("collections", "items", "attachments", "notes", "annotations")
        for row in conn.execute(f"SELECT key FROM {table} WHERE deleted_at IS NOT NULL")
    }


def test_import_bilingual_counts(database: sqlite3.Connection, bilingual_snapshot: Path) -> None:
    report = import_snapshot(database, bilingual_snapshot).import_report
    assert (
        report.counts.collections,
        report.counts.items,
        report.counts.items_trashed,
        report.counts.attachments,
        report.counts.notes,
        report.counts.annotations,
    ) == (3, 6, 1, 4, 2, 2)
    assert report.counts.attachments_by_availability == {
        "available": 2,
        "missing": 1,
        "invalid_path": 0,
        "remote": 1,
    }
    assert [(p.code, p.key) for p in report.problems] == [(ErrorCode.NOT_FOUND, "AT2MIS22")]


def test_reimport_creates_no_duplicates_and_keeps_ids(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    first = import_snapshot(database, bilingual_snapshot)
    ids = item_ids(database)
    second = import_snapshot(database, bilingual_snapshot)
    assert second.import_report.counts == first.import_report.counts
    assert second.changes == []
    assert item_ids(database) == ids


def test_partial_snapshot_tombstones_nothing(
    database: sqlite3.Connection, bilingual_snapshot: Path, tmp_path: Path
) -> None:
    complete = import_snapshot(database, bilingual_snapshot).import_report
    partial_dir = tmp_path / "partial"
    shutil.copytree(bilingual_snapshot, partial_dir)
    make_partial(partial_dir, "CL2ATTN2")
    data = load_manifest_dict(partial_dir)
    data["snapshot_id"] = "bilingual-partial"
    write_manifest_dict(partial_dir, data)
    report = import_snapshot(database, partial_dir)
    assert report.completeness == "partial"
    assert report.changes == []
    assert tombstoned(database) == set()
    assert report.import_report.counts == complete.counts


def test_failed_import_leaves_no_partial_state(
    database: sqlite3.Connection, bilingual_snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr("hyperwiki.services.library_import.upsert_note", boom)
    with pytest.raises(RuntimeError):
        import_snapshot(database, bilingual_snapshot)
    assert library_status(database).libraries == []
    assert database.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_status_reports_last_snapshot(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    report = import_snapshot(database, bilingual_snapshot).import_report
    [library] = library_status(database).libraries
    assert library.name == "合成双语文库 Synthetic bilingual library"
    assert library.last_snapshot_id == "bilingual-2026-11-02"
    assert library.last_completeness == "complete"
    assert library.counts == report.counts


def test_older_snapshot_does_not_overwrite_newer_records(
    database: sqlite3.Connection, bilingual_snapshot: Path, tmp_path: Path
) -> None:
    newer = tmp_path / "newer"
    shutil.copytree(bilingual_snapshot, newer)
    edit_item_title(newer, "IT2ENG22", "Revised title")
    set_trashed(newer, "item", "IT2ZHN22", True)
    data = load_manifest_dict(newer)
    data["snapshot_id"] = "bilingual-newer"
    next(i for i in data["items"] if i["key"] == "IT2ENG22")["tags"] = ["revised"]
    write_manifest_dict(newer, data)
    import_snapshot(database, newer)
    import_snapshot(database, bilingual_snapshot)
    rows = {
        row["key"]: row
        for row in database.execute("SELECT key, version, title, trashed FROM items")
    }
    assert (rows["IT2ENG22"]["version"], rows["IT2ENG22"]["title"]) == (2, "Revised title")
    assert rows["IT2ZHN22"]["trashed"] == 1
    tags = database.execute(
        "SELECT t.tag FROM item_tags AS t JOIN items AS i ON i.id = t.item_id"
        " WHERE i.key = 'IT2ENG22'"
    ).fetchall()
    assert [row["tag"] for row in tags] == ["revised"]


def test_reimport_at_same_version_refreshes_file_state(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    import_snapshot(database, bilingual_snapshot)
    (bilingual_snapshot / "storage" / "AT2MIS22").mkdir()
    (bilingual_snapshot / "storage" / "AT2MIS22" / "missing.pdf").write_bytes(b"%PDF-1.7")
    report = import_snapshot(database, bilingual_snapshot).import_report
    assert report.counts.attachments_by_availability["missing"] == 0
    assert report.counts.attachments_by_availability["available"] == 3


def test_snapshot_delete_tombstones(database: sqlite3.Connection, bilingual_snapshot: Path) -> None:
    import_snapshot(database, bilingual_snapshot)
    delete_item(bilingual_snapshot, "IT2ENG22")
    report = import_snapshot(database, bilingual_snapshot)
    assert report.change_counts == {
        "item": {"delete": 1},
        "attachment": {"delete": 1},
        "note": {"delete": 1},
        "annotation": {"delete": 1},
    }
    assert tombstoned(database) == {
        ("items", "IT2ENG22"),
        ("attachments", "AT2ENG22"),
        ("notes", "NT2ENG22"),
        ("annotations", "AN2ENG22"),
    }
    assert import_snapshot(database, bilingual_snapshot).changes == []


def test_counts_exclude_tombstoned(database: sqlite3.Connection, bilingual_snapshot: Path) -> None:
    first = import_snapshot(database, bilingual_snapshot).import_report.counts
    assert first.deleted == NO_DELETIONS
    delete_item(bilingual_snapshot, "IT2ENG22")
    counts = import_snapshot(database, bilingual_snapshot).import_report.counts
    assert (counts.items, counts.attachments, counts.notes, counts.annotations) == (
        first.items - 1,
        first.attachments - 1,
        first.notes - 1,
        first.annotations - 1,
    )
    assert counts.attachment_versions == first.attachment_versions - 1
    assert counts.deleted == {
        "collection": 0,
        "item": 1,
        "attachment": 1,
        "note": 1,
        "annotation": 1,
    }
    assert library_status(database).libraries[0].counts == counts


def test_reappearing_key_is_live_again(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    """A record restored, say from a backup, comes back even at a lower version."""
    original = next(
        i for i in load_manifest_dict(bilingual_snapshot)["items"] if i["key"] == "IT2ENG22"
    )
    import_snapshot(database, bilingual_snapshot)
    delete_item(bilingual_snapshot, "IT2ENG22")
    import_snapshot(database, bilingual_snapshot)
    data = load_manifest_dict(bilingual_snapshot)
    data["items"].append({**original, "version": 0, "title": "Restored from backup"})
    write_manifest_dict(bilingual_snapshot, data)
    report = import_snapshot(database, bilingual_snapshot)
    assert [(e.entity, e.change, e.key) for e in report.changes] == [("item", "upsert", "IT2ENG22")]
    row = database.execute(
        "SELECT title, version, deleted_at FROM items WHERE key = 'IT2ENG22'"
    ).fetchone()
    assert tuple(row) == ("Restored from backup", 0, None)
    assert tombstoned(database) == {
        ("attachments", "AT2ENG22"),
        ("notes", "NT2ENG22"),
        ("annotations", "AN2ENG22"),
    }
