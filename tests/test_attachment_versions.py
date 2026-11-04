import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hyperwiki.services.library_import import import_snapshot
from hyperwiki.storage.library_repo import (
    attachment_version_id,
    current_attachment_version,
    list_attachment_versions,
    stable_id,
)
from snapshot_mutations import (
    load_manifest_dict,
    replace_attachment_content,
    write_manifest_dict,
)

T1 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
T3 = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
ENGLISH = "AT2ENG22"


def _import(conn: sqlite3.Connection, snapshot: Path, at: datetime) -> str:
    return import_snapshot(conn, snapshot, now=lambda: at).library_id


def _attachment_id(library_id: str, key: str = ENGLISH) -> str:
    return stable_id(library_id, "attachment", key)


def _english_file(snapshot: Path) -> Path:
    data = load_manifest_dict(snapshot)
    path: str = next(a["path"] for a in data["attachments"] if a["key"] == ENGLISH)
    storage_root: str = data["storage_root"]
    return snapshot / storage_root / path


def test_import_records_one_version_per_available_attachment(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = _import(database, bilingual_snapshot, T1)
    first = {
        key: list_attachment_versions(database, _attachment_id(library_id, key))
        for key in ("AT2ENG22", "AT2ZHN22", "AT2MIS22")
    }
    assert [len(versions) for versions in first.values()] == [1, 1, 0]

    report = import_snapshot(database, bilingual_snapshot, now=lambda: T2)
    assert report.import_report.counts.attachment_versions == 2
    for key, versions in first.items():
        again = list_attachment_versions(database, _attachment_id(library_id, key))
        assert [v.id for v in again] == [v.id for v in versions]
        assert all(v.first_seen_at == T1 and v.last_seen_at == T2 for v in again)

    english = first["AT2ENG22"][0]
    content = _english_file(bilingual_snapshot).read_bytes()
    assert english.sha256 == hashlib.sha256(content).hexdigest()
    assert english.size_bytes == len(content)
    assert english.id == attachment_version_id(english.attachment_id, english.sha256)


def test_replaced_pdf_adds_version_and_becomes_current(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = _import(database, bilingual_snapshot, T1)
    original = current_attachment_version(database, _attachment_id(library_id))
    assert original is not None

    replace_attachment_content(bilingual_snapshot, ENGLISH, b"replacement bytes")
    _import(database, bilingual_snapshot, T2)

    versions = list_attachment_versions(database, _attachment_id(library_id))
    current = current_attachment_version(database, _attachment_id(library_id))
    assert [v.id for v in versions] == [original.id, current.id if current else None]
    assert current is not None
    assert current.sha256 == hashlib.sha256(b"replacement bytes").hexdigest()
    assert current.first_seen_at == T2


def test_restored_bytes_reuse_old_version(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    original_bytes = _english_file(bilingual_snapshot).read_bytes()
    library_id = _import(database, bilingual_snapshot, T1)
    original = current_attachment_version(database, _attachment_id(library_id))
    assert original is not None

    replace_attachment_content(bilingual_snapshot, ENGLISH, b"replacement bytes")
    _import(database, bilingual_snapshot, T2)
    replace_attachment_content(bilingual_snapshot, ENGLISH, original_bytes)
    _import(database, bilingual_snapshot, T3)

    versions = list_attachment_versions(database, _attachment_id(library_id))
    current = current_attachment_version(database, _attachment_id(library_id))
    assert len(versions) == 2
    assert current is not None
    assert current.id == original.id
    assert (current.first_seen_at, current.last_seen_at) == (T1, T3)


def test_missing_file_has_no_current_version_but_keeps_history(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = _import(database, bilingual_snapshot, T1)
    _english_file(bilingual_snapshot).unlink()
    _import(database, bilingual_snapshot, T2)

    assert current_attachment_version(database, _attachment_id(library_id)) is None
    assert len(list_attachment_versions(database, _attachment_id(library_id))) == 1


def test_stale_attachment_version_records_nothing(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = _import(database, bilingual_snapshot, T1)
    original = current_attachment_version(database, _attachment_id(library_id))

    data = load_manifest_dict(bilingual_snapshot)
    attachment = next(a for a in data["attachments"] if a["key"] == ENGLISH)
    attachment["version"] = 0
    write_manifest_dict(bilingual_snapshot, data)
    _english_file(bilingual_snapshot).write_bytes(b"bytes from an older snapshot")
    _import(database, bilingual_snapshot, T2)

    assert current_attachment_version(database, _attachment_id(library_id)) == original
    assert len(list_attachment_versions(database, _attachment_id(library_id))) == 1
