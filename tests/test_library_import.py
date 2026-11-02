import shutil
import sqlite3
from pathlib import Path

import pytest

from hyperwiki.errors import ErrorCode
from hyperwiki.services.library_import import import_snapshot, library_status
from snapshot_mutations import (
    edit_item_title,
    load_manifest_dict,
    make_partial,
    set_trashed,
    write_manifest_dict,
)


def item_ids(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row["key"], row["id"]) for row in conn.execute("SELECT key, id FROM items ORDER BY key")
    ]


def test_import_bilingual_counts(database: sqlite3.Connection, bilingual_snapshot: Path) -> None:
    report = import_snapshot(database, bilingual_snapshot)
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
    assert second.counts == first.counts
    assert item_ids(database) == ids


def test_partial_after_complete_deletes_nothing(
    database: sqlite3.Connection, bilingual_snapshot: Path, tmp_path: Path
) -> None:
    complete = import_snapshot(database, bilingual_snapshot)
    partial_dir = tmp_path / "partial"
    shutil.copytree(bilingual_snapshot, partial_dir)
    make_partial(partial_dir, "CL2ATTN2")
    data = load_manifest_dict(partial_dir)
    data["snapshot_id"] = "bilingual-partial"
    write_manifest_dict(partial_dir, data)
    report = import_snapshot(database, partial_dir)
    assert report.completeness == "partial"
    assert report.counts == complete.counts


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
    report = import_snapshot(database, bilingual_snapshot)
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
    report = import_snapshot(database, bilingual_snapshot)
    assert report.counts.attachments_by_availability["missing"] == 0
    assert report.counts.attachments_by_availability["available"] == 3
