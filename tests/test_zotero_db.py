import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from hyperwiki.adapters import snapshot, zotero_db
from hyperwiki.adapters.snapshot import LoadedSnapshot
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import profile_root
from hyperwiki.domain.library import Creator, KnownFile
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.interfaces import cli
from hyperwiki.interfaces.cli import DATABASE_NAME, main
from hyperwiki.services.library_import import import_loaded
from hyperwiki.storage.database import open_database
from hyperwiki.storage.library_repo import known_attachment_files, stable_id
from pdf_factory import make_pdf
from test_cli import run_cli
from zotero_db_factory import sample_library

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


def test_trashed_collections_are_read_as_trashed(zotero_dir: Path) -> None:
    """A trashed collection still exists in Zotero: reading it as absent would look deleted."""
    manifest = read(zotero_dir).manifest
    collections = by_key(manifest.collections)
    assert sorted(collections) == ["CLCHLD22", "CLREAD22", "CLTRCH22", "CLTRSH22"]
    assert collections["CLCHLD22"].parent_key == "CLREAD22"
    assert collections["CLCHLD22"].name == "中文文献"
    assert collections["CLREAD22"].version == 4
    assert not collections["CLREAD22"].trashed and not collections["CLCHLD22"].trashed
    assert collections["CLTRSH22"].trashed
    # A subcollection of a trashed collection is in the trash with it.
    assert collections["CLTRCH22"].trashed
    assert collections["CLTRCH22"].parent_key == "CLTRSH22"
    items = by_key(manifest.items)
    assert items["ITENGL22"].collection_keys == ["CLREAD22", "CLTRSH22"]
    assert items["ITCHIN22"].collection_keys == ["CLCHLD22"]


def test_children_of_trashed_items_are_trashed(tmp_path: Path) -> None:
    """Zotero's trash lists the children of a trashed item; only the parent is in deletedItems."""
    db = sample_library(tmp_path / "Zotero")
    db.attachment("ATTRSH22", parent="ITTRSH22", link_mode=0, path="storage:t.pdf", content=b"t")
    db.note("NTTRSH22", parent="ITTRSH22", html="<p>child of a trashed item</p>")
    db.annotation("ANTRCH22", parent="ATTRSH22", text="on a trashed attachment")
    db.attachment("ATSELF22", parent="ITCHIN22", link_mode=2, path="/x.pdf", trashed=True)
    db.close()
    manifest = read(tmp_path / "Zotero").manifest
    attachments = by_key(manifest.attachments)
    assert attachments["ATTRSH22"].trashed
    assert attachments["ATSELF22"].trashed
    assert not attachments["ATCHIN22"].trashed
    notes = by_key(manifest.notes)
    assert notes["NTTRSH22"].trashed
    assert not notes["NTCHLD22"].trashed
    annotations = by_key(manifest.annotations)
    assert annotations["ANTRCH22"].trashed
    assert not annotations["ANHIGH22"].trashed


def test_collection_cycle_is_reported_as_problems(tmp_path: Path) -> None:
    """Skipped collections are named in problems, so a later diff never reads them as deleted."""
    db = sample_library(tmp_path / "Zotero")
    db.collection("CLCYCA22", "A")
    db.collection("CLCYCB22", "B", parent="CLCYCA22")
    db.conn.execute(
        "UPDATE collections SET parentCollectionID = ? WHERE key = 'CLCYCA22'",
        (db.id("CLCYCB22"),),
    )
    db.close()
    loaded = read(tmp_path / "Zotero")
    assert "CLCYCA22" not in by_key(loaded.manifest.collections)
    assert {(p.entity, p.key) for p in loaded.problems} >= {
        ("collection", "CLCYCA22"),
        ("collection", "CLCYCB22"),
    }


def test_resolves_base_relative_linked_files(tmp_path: Path) -> None:
    """Zotero stores files inside the Linked Attachment Base Directory as ``attachments:``."""
    base = tmp_path / "论文库"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "中文.pdf").write_bytes(b"%PDF base relative")
    db = sample_library(tmp_path / "Zotero")
    db.attachment("ATBASE22", parent="ITCHIN22", link_mode=2, path="attachments:sub/中文.pdf")
    db.close()
    loaded = read_zotero_library(tmp_path / "Zotero", base_attachment_dir=base, now=lambda: NOW)
    attachment = by_key(loaded.manifest.attachments)["ATBASE22"]
    assert attachment.link_mode == "linked_file"
    assert attachment.path == str(base / "sub" / "中文.pdf")
    file = loaded.files["ATBASE22"]
    assert file.availability == "available"
    assert file.sha256 == hashlib.sha256(b"%PDF base relative").hexdigest()
    assert "ATBASE22" not in {p.key for p in loaded.problems}


@pytest.mark.parametrize(
    "stored",
    [
        "attachments:../outside.pdf",
        "attachments:sub/../../outside.pdf",
        "attachments:/abs/outside.pdf",
        "attachments:sub\\..\\..\\outside.pdf",
        "attachments:",
    ],
)
def test_base_relative_path_cannot_escape(tmp_path: Path, stored: str) -> None:
    base = tmp_path / "base"
    (base / "sub").mkdir(parents=True)
    (tmp_path / "outside.pdf").write_bytes(b"must not be read")
    db = sample_library(tmp_path / "Zotero")
    db.attachment("ATBASE22", parent="ITCHIN22", link_mode=2, path=stored)
    db.close()
    loaded = read_zotero_library(tmp_path / "Zotero", base_attachment_dir=base, now=lambda: NOW)
    assert loaded.files["ATBASE22"].availability == "invalid_path"
    assert loaded.files["ATBASE22"].sha256 is None
    assert ("attachment", "ATBASE22", ErrorCode.INVALID_PATH) in {
        (p.entity, p.key, p.code) for p in loaded.problems
    }


def test_base_relative_symlink_cannot_escape(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "outside.pdf").write_bytes(b"must not be read")
    try:
        (base / "link.pdf").symlink_to(tmp_path / "outside.pdf")
    except OSError:
        pytest.skip("symlinks not available")
    db = sample_library(tmp_path / "Zotero")
    db.attachment("ATBASE22", parent="ITCHIN22", link_mode=2, path="attachments:link.pdf")
    db.close()
    loaded = read_zotero_library(tmp_path / "Zotero", base_attachment_dir=base, now=lambda: NOW)
    assert loaded.files["ATBASE22"].availability == "invalid_path"


def test_base_relative_without_base_dir_is_invalid_path(tmp_path: Path) -> None:
    db = sample_library(tmp_path / "Zotero")
    db.attachment("ATBASE22", parent="ITCHIN22", link_mode=2, path="attachments:sub/a.pdf")
    db.close()
    loaded = read(tmp_path / "Zotero")
    attachment = by_key(loaded.manifest.attachments)["ATBASE22"]
    assert attachment.path == "attachments:sub/a.pdf"
    assert loaded.files["ATBASE22"].availability == "invalid_path"
    [problem] = [p for p in loaded.problems if p.key == "ATBASE22"]
    assert problem.code == ErrorCode.INVALID_PATH
    assert "Linked Attachment Base Directory" in problem.message


def known_from(loaded: LoadedSnapshot) -> dict[str, KnownFile]:
    paths = {a.key: a.path for a in loaded.manifest.attachments}
    return {
        key: KnownFile(
            path=paths[key], size_bytes=f.size_bytes, mtime_ns=f.mtime_ns, sha256=f.sha256
        )
        for key, f in loaded.files.items()
        if f.sha256 is not None and f.size_bytes is not None and f.mtime_ns is not None
    }


def counting_hash(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    hashed: list[Path] = []
    original = snapshot._hash_file

    def counted(path: Path) -> tuple[str, int]:
        hashed.append(path)
        return original(path)

    monkeypatch.setattr(snapshot, "_hash_file", counted)
    return hashed


def test_unchanged_file_is_not_rehashed(zotero_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = read(zotero_dir)
    known = known_from(first)
    assert sorted(known) == ["ATCHIN22", "ATENGL22"]
    hashed = counting_hash(monkeypatch)
    second = read_zotero_library(zotero_dir, known_files=known, now=lambda: NOW)
    assert hashed == []
    assert second.files == first.files


def test_changed_file_is_rehashed(zotero_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    known = known_from(read(zotero_dir))
    pdf = zotero_dir / "storage" / "ATENGL22" / "attention.pdf"
    stat = pdf.stat()
    # Same size, new bytes, new modification time: a PDF replaced on disk outside Zotero.
    pdf.write_bytes(b"%PDF-1.4 synthetic ENGLISH")
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    hashed = counting_hash(monkeypatch)
    loaded = read_zotero_library(zotero_dir, known_files=known, now=lambda: NOW)
    assert [path.name for path in hashed] == ["attention.pdf"]
    assert loaded.files["ATENGL22"].sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert loaded.files["ATENGL22"].mtime_ns == pdf.stat().st_mtime_ns


def test_moved_file_with_same_stat_is_rehashed(
    zotero_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known hash is reused only for the same stored path."""
    known = known_from(read(zotero_dir))
    known["ATENGL22"] = known["ATENGL22"].model_copy(update={"path": "ATENGL22/old.pdf"})
    hashed = counting_hash(monkeypatch)
    read_zotero_library(zotero_dir, known_files=known, now=lambda: NOW)
    assert [path.name for path in hashed] == ["attention.pdf"]


def test_import_stores_file_mtime(database: sqlite3.Connection, zotero_dir: Path) -> None:
    loaded = read(zotero_dir)
    report = import_loaded(database, loaded, now=lambda: NOW)
    assert known_attachment_files(database, report.library_id) == known_from(loaded)


def test_trashed_annotations_are_read_as_trashed(zotero_dir: Path) -> None:
    annotations = by_key(read(zotero_dir).manifest.annotations)
    assert sorted(annotations) == ["ANHIGH22", "ANTRSH22"]
    assert annotations["ANTRSH22"].trashed
    assert annotations["ANTRSH22"].text == "removed"


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

    annotation = by_key(loaded.manifest.annotations)["ANHIGH22"]
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
    ) == (4, 4, 1, 4, 2, 2, 2)
    ids = database.execute("SELECT key, id FROM items ORDER BY key").fetchall()
    assert dict(ids)["ITENGL22"] == stable_id(first.library_id, "item", "ITENGL22")
    trashed = database.execute(
        "SELECT key FROM collections WHERE trashed = 1 UNION ALL "
        "SELECT key FROM annotations WHERE trashed = 1 ORDER BY key"
    ).fetchall()
    assert [row[0] for row in trashed] == ["ANTRSH22", "CLTRCH22", "CLTRSH22"]

    second = import_loaded(database, read(zotero_dir), now=lambda: NOW)
    assert second.counts == counts
    assert second.library_id == first.library_id
    assert database.execute("SELECT key, id FROM items ORDER BY key").fetchall() == ids


def test_cli_sync_from_zotero(tmp_path: Path, zotero_dir: Path) -> None:
    ws = str(tmp_path / "ws")
    code, payload, _ = run_cli(["--workspace", ws, "sync", str(zotero_dir)])
    assert code == 0 and payload["ok"] is True
    result = payload["result"]
    assert (result["trigger"], result["paused"], result["selected"]) == ("all", False, None)
    assert result["sync"]["counts"]["items"] == 4
    assert result["sync"]["snapshot_id"] == "zotero-20"
    [plan] = result["plans"].values()
    assert plan["queued"] == result["worker"]["done"] + result["worker"]["failed"]
    code, payload, _ = run_cli(["--workspace", ws, "status"])
    assert payload["result"]["libraries"][0]["adapter"] == "zotero"
    assert payload["result"]["updates"]["last_full_check_at"] is not None
    conn = open_database(Path(ws) / DATABASE_NAME)
    try:
        row = conn.execute("SELECT data_json FROM items WHERE key = 'ITCHIN22'").fetchone()
    finally:
        conn.close()
    assert json.loads(row["data_json"])["title"] == "注意力机制研究"


def test_cli_sync_reports_changes(tmp_path: Path, zotero_dir: Path) -> None:
    ws = str(tmp_path / "ws")
    code, payload, _ = run_cli(["--workspace", ws, "sync", str(zotero_dir)])
    assert code == 0
    result = payload["result"]["sync"]
    assert result["change_counts"]["item"] == {"upsert": 4}
    assert "changes" not in result
    assert result["duration_ms"] >= 0

    conn = sqlite3.connect(zotero_dir / "zotero.sqlite")
    conn.execute("UPDATE items SET clientVersion = 50 WHERE key = 'ITENGL22'")
    conn.commit()
    conn.close()
    code, payload, _ = run_cli(["--workspace", ws, "sync", str(zotero_dir), "--changes"])
    assert code == 0
    result = payload["result"]["sync"]
    assert result["change_counts"] == {"item": {"upsert": 1}}
    assert [(c["entity"], c["key"], c["change"], c["version"]) for c in result["changes"]] == [
        ("item", "ITENGL22", "upsert", 50)
    ]


def test_cli_sync_selected_force(tmp_path: Path) -> None:
    db = sample_library(tmp_path / "Zotero")
    db.attachment(
        "ATREAL22",
        parent="ITCASE22",
        link_mode=0,
        path="storage:case.pdf",
        content=make_pdf([["A synthetic case summary with enough text to keep."]]),
    )
    db.close()
    args = ["--workspace", str(tmp_path / "ws"), "sync", str(tmp_path / "Zotero")]
    run_cli(args)
    code, payload, _ = run_cli([*args, "--item", "ITCASE22"])
    assert code == 0
    result = payload["result"]
    assert result["trigger"] == "selected"
    [selected] = result["selected"].values()
    assert (selected["queued"], selected["already_done"]) == (0, 1)
    assert result["worker"]["done"] == 0
    code, payload, _ = run_cli([*args, "--item", "ITCASE22", "--force"])
    [selected] = payload["result"]["selected"].values()
    assert selected["queued"] == 1
    assert payload["result"]["worker"] == {"done": 1, "retried": 0, "failed": 0, "lost": 0}


def test_cli_sync_unknown_selection_is_not_found(tmp_path: Path, zotero_dir: Path) -> None:
    args = ["--workspace", str(tmp_path / "ws"), "sync", str(zotero_dir)]
    code, payload, _ = run_cli([*args, "--collection", "CLMISS22"])
    assert code == 1
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["details"] == {"item_keys": [], "collection_keys": ["CLMISS22"]}


def test_cli_sync_force_needs_selection(tmp_path: Path, zotero_dir: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--workspace", str(tmp_path / "ws"), "sync", str(zotero_dir), "--force"])
    assert raised.value.code == 2
    with pytest.raises(SystemExit):
        main(["--workspace", str(tmp_path / "ws"), "sync", "--item", "not-a-key"])
    assert not (tmp_path / "ws").exists()


def test_cli_sync_discovers_data_dir(tmp_path: Path, zotero_dir: Path) -> None:
    home, appdata = tmp_path / "home", tmp_path / "appdata"
    environ = {"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(appdata)}
    root = profile_root(home=home, environ=environ, platform=sys.platform)
    profile = root / "Profiles" / "abcd1234.default"
    profile.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=default\nIsRelative=1\nPath=Profiles/abcd1234.default\nDefault=1\n",
        encoding="utf-8",
    )
    (profile / "prefs.js").write_text(
        f'user_pref("extensions.zotero.dataDir", {json.dumps(str(zotero_dir))});\n'
        'user_pref("extensions.zotero.useDataDir", true);\n',
        encoding="utf-8",
    )
    code, payload, _ = run_cli(["--workspace", str(tmp_path / "ws"), "sync"], environ)
    assert code == 0, payload
    assert payload["result"]["sync"]["counts"]["items"] == 4


def test_cli_sync_needs_data_dir_without_home(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    code, payload, _ = run_cli(["--workspace", str(ws), "sync"])
    assert code == 1
    assert payload["error"]["code"] == "not_found"
    assert not ws.exists()


def test_cli_sync_base_dir(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "a.pdf").write_bytes(b"%PDF base")
    db = sample_library(tmp_path / "Zotero")
    db.attachment("ATBASE22", parent="ITCHIN22", link_mode=2, path="attachments:a.pdf")
    db.close()
    args = ["--workspace", str(tmp_path / "ws"), "sync", str(tmp_path / "Zotero")]
    code, payload, _ = run_cli([*args, "--base-dir", str(base)])
    assert code == 0
    assert payload["result"]["sync"]["counts"]["attachments_by_availability"]["available"] == 3


def test_cli_watch_writes_json_lines(tmp_path: Path, zotero_dir: Path) -> None:
    out = io.BytesIO()
    args = ["--workspace", str(tmp_path / "ws"), "watch", str(zotero_dir)]
    args += ["--interval", "0", "--quiet-period", "0", "--rescan-interval", "0"]
    code = main([*args, "--max-syncs", "2"], stdout=out, environ={})
    assert code == 0
    lines = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    assert [line["ok"] for line in lines] == [True, True]
    assert [line["result"]["trigger"] for line in lines] == ["scheduled", "automatic"]
    assert lines[0]["result"]["sync"]["change_counts"]["item"] == {"upsert": 4}
    assert lines[1]["result"]["sync"]["change_counts"] == {}


def interrupt(*args: object, **kwargs: object) -> None:
    raise KeyboardInterrupt


def test_cli_interrupted_watch_exits_cleanly(
    tmp_path: Path, zotero_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "watch_updates", interrupt)
    out = io.BytesIO()
    args = ["--workspace", str(tmp_path / "ws"), "watch", str(zotero_dir)]
    assert main(args, stdout=out, environ={}) == 0
    assert out.getvalue() == b""


def test_cli_interrupted_sync_is_not_reported_as_success(
    tmp_path: Path, zotero_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "update_zotero", interrupt)
    with pytest.raises(KeyboardInterrupt):
        main(["--workspace", str(tmp_path / "ws"), "sync", str(zotero_dir)], environ={})


def test_cli_sync_without_database_creates_no_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    code, payload, _ = run_cli(["--workspace", str(ws), "sync", str(tmp_path / "none")])
    assert code == 1
    assert payload["error"]["code"] == "not_found"
    assert not ws.exists()
