import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from hyperwiki.adapters import zotero_db
from hyperwiki.adapters.snapshot import LoadedSnapshot
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.domain.library import Creator
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.interfaces.cli import DATABASE_NAME
from hyperwiki.services.library_import import import_loaded
from hyperwiki.storage.database import open_database
from hyperwiki.storage.library_repo import stable_id
from test_cli import run_cli
from zotero_db_factory import ZoteroDb, sample_library

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "Zotero"
    sample_library(data_dir).close()
    return data_dir


class Keyed(Protocol):
    @property
    def key(self) -> str: ...


def read(data_dir: Path) -> LoadedSnapshot:
    return read_zotero_library(data_dir, now=lambda: NOW)


def by_key[T: Keyed](records: Sequence[T]) -> dict[str, T]:
    return {record.key: record for record in records}


def test_reads_items_with_zotero_field_mapping(zotero_dir: Path) -> None:
    items = by_key(read(zotero_dir).manifest.items)
    assert sorted(items) == ["ITCASE22", "ITCHIN22", "ITENGL22", "ITTRSH22"]

    english = items["ITENGL22"]
    assert (english.item_type, english.title, english.date) == (
        "journalArticle",
        "Attention study",
        "June 2020",
    )
    assert (english.doi, english.url, english.abstract, english.language) == (
        "10.1234/attention",
        "https://example.org/attention",
        "We study attention.",
        "en",
    )
    assert english.creators == [
        Creator(creator_type="author", first_name="Jane", last_name="Smith"),
        Creator(creator_type="editor", name="Synthetic Lab"),
    ]
    assert english.tags == ["transformers", "注意力"]
    assert english.extra == {
        "arXiv": "2006.00001",
        "Citation Key": "smith2020",
        "text": "free text line",
    }
    assert english.date_modified == datetime(2026, 4, 2, 3, 34, 21, tzinfo=UTC)
    assert not english.trashed

    chinese = items["ITCHIN22"]
    assert (chinese.title, chinese.date, chinese.language) == ("注意力机制研究", "2021", "zh")
    assert chinese.creators == [Creator(creator_type="author", first_name="小明", last_name="王")]
    assert items["ITCASE22"].title == "Smith v. Jones"
    assert items["ITTRSH22"].trashed


def test_reads_collections_excluding_trashed_subtrees(zotero_dir: Path) -> None:
    manifest = read(zotero_dir).manifest
    collections = by_key(manifest.collections)
    assert sorted(collections) == ["CLCHLD22", "CLREAD22"]
    assert collections["CLCHLD22"].parent_key == "CLREAD22"
    assert collections["CLCHLD22"].name == "中文文献"
    assert collections["CLREAD22"].version == 4
    items = by_key(manifest.items)
    assert items["ITENGL22"].collection_keys == ["CLREAD22"]
    assert items["ITCHIN22"].collection_keys == ["CLCHLD22"]


def test_reads_attachments_notes_and_annotations(zotero_dir: Path) -> None:
    loaded = read(zotero_dir)
    attachments = by_key(loaded.manifest.attachments)
    assert sorted(attachments) == ["ATCHIN22", "ATENGL22", "ATLINK22", "ATMISS22"]
    english = attachments["ATENGL22"]
    assert (english.link_mode, english.path, english.url, english.parent_key) == (
        "imported_url",
        "ATENGL22/attention.pdf",
        "https://example.org/attention.pdf",
        "ITENGL22",
    )
    assert attachments["ATCHIN22"].path == "ATCHIN22/中文论文.pdf"
    link = attachments["ATLINK22"]
    assert (link.link_mode, link.path, link.url, link.content_type) == (
        "linked_url",
        None,
        "https://example.org/page",
        "",
    )

    files = loaded.files
    assert files["ATENGL22"].availability == "available"
    assert files["ATENGL22"].sha256 == hashlib.sha256(b"%PDF-1.4 synthetic english").hexdigest()
    assert files["ATCHIN22"].availability == "available"
    assert files["ATMISS22"].availability == "missing"
    assert files["ATLINK22"].availability == "remote"
    assert [(p.code, p.key) for p in loaded.problems] == [(ErrorCode.NOT_FOUND, "ATMISS22")]

    notes = by_key(loaded.manifest.notes)
    assert sorted(notes) == ["NTCHLD22", "NTSTND22"]
    assert (notes["NTCHLD22"].parent_key, notes["NTCHLD22"].html) == (
        "ITENGL22",
        "<p>My note 我的笔记</p>",
    )
    assert notes["NTSTND22"].parent_key is None

    [annotation] = loaded.manifest.annotations
    assert (annotation.key, annotation.parent_key, annotation.annotation_type) == (
        "ANHIGH22",
        "ATENGL22",
        "highlight",
    )
    assert (annotation.text, annotation.comment, annotation.page_label, annotation.color) == (
        "highlighted",
        "why",
        "3",
        "#ffd400",
    )


def test_uses_client_version_and_local_user_key(zotero_dir: Path) -> None:
    loaded = read(zotero_dir)
    manifest = loaded.manifest
    assert manifest.adapter == "zotero"
    assert (manifest.library.id, manifest.library.kind, manifest.library.name) == (
        "local:AbCdEfGh",
        "user",
        "My Library",
    )
    assert (manifest.snapshot_id, manifest.created_at, manifest.completeness) == (
        "zotero-20",
        NOW,
        "complete",
    )
    assert by_key(manifest.items)["ITENGL22"].version == 12
    assert by_key(manifest.attachments)["ATENGL22"].version == 13
    assert loaded.directory == zotero_dir.resolve()


def _tree_state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_read_leaves_zotero_data_dir_untouched(tmp_path: Path) -> None:
    data_dir = tmp_path / "Zotero"
    db = sample_library(data_dir)
    try:
        # Zotero running: a WAL-mode writer whose latest commit is still only in the WAL.
        db.conn.execute("PRAGMA journal_mode = WAL")
        db.conn.execute("PRAGMA wal_autocheckpoint = 0")
        db.item("ITLATE22", fields={"title": "Added while Zotero runs"})
        db.commit()
        assert (data_dir / "zotero.sqlite-wal").stat().st_size > 0
        before = _tree_state(data_dir)

        loaded = read(data_dir)

        assert _tree_state(data_dir) == before
        assert "ITLATE22" in by_key(loaded.manifest.items)
    finally:
        db.close()


def test_changing_database_raises_busy(zotero_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def copy_while_zotero_writes(source: Path, target: Path) -> None:
        shutil.copyfile(source, target)
        if source.name == "zotero.sqlite":
            with source.open("ab") as handle:
                handle.write(b"\0")

    monkeypatch.setattr(zotero_db, "_copy_file", copy_while_zotero_writes)
    with pytest.raises(HyperWikiError) as caught:
        read(zotero_dir)
    assert caught.value.code is ErrorCode.ZOTERO_DATABASE_BUSY
    assert caught.value.details == {"attempts": 3}


def test_copy_is_retried_after_one_change(
    zotero_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def copy_once_changing(source: Path, target: Path) -> None:
        shutil.copyfile(source, target)
        calls.append(source.name)
        if calls == ["zotero.sqlite"]:
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(zotero_db, "_copy_file", copy_once_changing)
    assert len(read(zotero_dir).manifest.items) == 4
    assert calls == ["zotero.sqlite", "zotero.sqlite"]


def test_missing_database_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(HyperWikiError) as caught:
        read(tmp_path)
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_missing_required_column_is_unsupported(zotero_dir: Path) -> None:
    conn = sqlite3.connect(zotero_dir / "zotero.sqlite")
    conn.execute("ALTER TABLE items DROP COLUMN clientVersion")
    conn.commit()
    conn.close()
    with pytest.raises(HyperWikiError) as caught:
        read(zotero_dir)
    assert caught.value.code is ErrorCode.UNSUPPORTED_ZOTERO_DATABASE
    assert caught.value.details == {"missing": ["items.clientVersion"], "userdata_version": 130}


def test_not_a_database_is_unsupported(tmp_path: Path) -> None:
    (tmp_path / "zotero.sqlite").write_bytes(b"this is not a database" * 100)
    with pytest.raises(HyperWikiError) as caught:
        read(tmp_path)
    assert caught.value.code is ErrorCode.UNSUPPORTED_ZOTERO_DATABASE


def test_invalid_records_are_skipped_with_problems(tmp_path: Path) -> None:
    data_dir = tmp_path / "Zotero"
    db = sample_library(data_dir)
    db.item("bad-key", fields={"title": "Invalid key"})
    db.attachment("ATBADK22", parent="bad-key", link_mode=0, path="storage:x.pdf", content=b"x")
    db.note("NTBADK22", parent="bad-key", html="<p>orphan</p>")
    db.item("ITNNAM22", fields={"title": "Nameless creator"}, creators=[("author", "", "", 0)])
    db.attachment("ATNPTH22", parent="ITNNAM22", link_mode=0, path=None)
    db.annotation("ANUNKN22", parent="ATENGL22", annotation_type=99, text="?")
    db.close()

    loaded = read(data_dir)
    manifest = loaded.manifest
    assert "ITNNAM22" in by_key(manifest.items)
    assert by_key(manifest.items)["ITNNAM22"].creators == []
    assert "bad-key" not in by_key(manifest.items)
    assert "ATBADK22" not in by_key(manifest.attachments)
    assert "ATNPTH22" not in by_key(manifest.attachments)
    assert "NTBADK22" not in by_key(manifest.notes)
    assert "ANUNKN22" not in by_key(manifest.annotations)
    assert len(manifest.items) == 5
    problems = {(p.entity, p.key): p.code for p in loaded.problems}
    assert problems == {
        ("annotation", "ANUNKN22"): ErrorCode.INVALID_DOCUMENT,
        ("attachment", "ATBADK22"): ErrorCode.INVALID_DOCUMENT,
        ("attachment", "ATMISS22"): ErrorCode.NOT_FOUND,
        ("attachment", "ATNPTH22"): ErrorCode.INVALID_DOCUMENT,
        ("item", "ITNNAM22"): ErrorCode.INVALID_DOCUMENT,
        ("item", "bad-key"): ErrorCode.INVALID_DOCUMENT,
        ("note", "NTBADK22"): ErrorCode.INVALID_DOCUMENT,
    }


def test_import_zotero_end_to_end_and_reimport_is_stable(
    database: sqlite3.Connection, zotero_dir: Path
) -> None:
    first = import_loaded(database, read(zotero_dir), now=lambda: NOW)
    counts = first.counts
    assert (
        counts.collections,
        counts.items,
        counts.items_trashed,
        counts.attachments,
        counts.attachment_versions,
        counts.notes,
        counts.annotations,
    ) == (2, 4, 1, 4, 2, 2, 1)
    ids = database.execute("SELECT key, id FROM items ORDER BY key").fetchall()
    assert dict(ids)["ITENGL22"] == stable_id(first.library_id, "item", "ITENGL22")

    second = import_loaded(database, read(zotero_dir), now=lambda: NOW)
    assert second.counts == counts
    assert second.library_id == first.library_id
    assert database.execute("SELECT key, id FROM items ORDER BY key").fetchall() == ids


def test_cli_import_zotero(tmp_path: Path, zotero_dir: Path) -> None:
    ws = str(tmp_path / "ws")
    code, payload, _ = run_cli(["--workspace", ws, "import-zotero", str(zotero_dir)])
    assert code == 0 and payload["ok"] is True
    assert payload["result"]["counts"]["items"] == 4
    assert payload["result"]["snapshot_id"] == "zotero-20"
    code, payload, _ = run_cli(["--workspace", ws, "status"])
    assert payload["result"]["libraries"][0]["adapter"] == "zotero"
    conn = open_database(Path(ws) / DATABASE_NAME)
    try:
        row = conn.execute("SELECT data_json FROM items WHERE key = 'ITCHIN22'").fetchone()
    finally:
        conn.close()
    assert json.loads(row["data_json"])["title"] == "注意力机制研究"


def test_cli_import_zotero_without_database_creates_no_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    code, payload, _ = run_cli(["--workspace", str(ws), "import-zotero", str(tmp_path / "none")])
    assert code == 1
    assert payload["error"]["code"] == "not_found"
    assert not ws.exists()


def test_factory_round_trip_sanity(tmp_path: Path) -> None:
    db = ZoteroDb.create(tmp_path / "Z")
    db.close()
    assert read(tmp_path / "Z").manifest.items == []
