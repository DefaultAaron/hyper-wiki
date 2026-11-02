import hashlib
import json
from pathlib import Path

from hyperwiki.domain.common import parse_document
from hyperwiki.domain.library import LibraryManifest
from snapshot_mutations import (
    BILINGUAL_DIR,
    delete_item,
    edit_item_title,
    load_manifest_dict,
    make_partial,
    replace_attachment_content,
    set_trashed,
)

BILINGUAL = BILINGUAL_DIR


def load(snapshot_dir: Path) -> LibraryManifest:
    return parse_document(LibraryManifest, load_manifest_dict(snapshot_dir))


def counts(manifest: LibraryManifest) -> tuple[int, int, int, int, int]:
    return (
        len(manifest.collections),
        len(manifest.items),
        len(manifest.attachments),
        len(manifest.notes),
        len(manifest.annotations),
    )


def test_bilingual_fixture_parses() -> None:
    manifest = load(BILINGUAL)
    assert counts(manifest) == (3, 6, 4, 2, 2)
    assert sum(item.trashed for item in manifest.items) == 1


def test_fixture_checksums_match() -> None:
    expected: dict[str, str] = json.loads(
        (BILINGUAL / "checksums.json").read_text(encoding="utf-8")
    )
    storage = BILINGUAL / "storage"
    actual = {
        p.relative_to(storage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in storage.rglob("*")
        if p.is_file()
    }
    assert actual == expected


def test_missing_pdf_is_absent() -> None:
    assert not (BILINGUAL / "storage" / "AT2MIS22" / "missing.pdf").exists()


def test_edit_item_title(bilingual_snapshot: Path) -> None:
    edit_item_title(bilingual_snapshot, "IT2ENG22", "Revised")
    item = next(i for i in load(bilingual_snapshot).items if i.key == "IT2ENG22")
    assert (item.title, item.version) == ("Revised", 2)


def test_replace_attachment_content(bilingual_snapshot: Path) -> None:
    replace_attachment_content(bilingual_snapshot, "AT2ENG22", b"new")
    path = bilingual_snapshot / "storage" / "AT2ENG22" / "attention-study.txt"
    assert path.read_bytes() == b"new"
    attachment = next(a for a in load(bilingual_snapshot).attachments if a.key == "AT2ENG22")
    assert attachment.version == 2


def test_trash_and_restore(bilingual_snapshot: Path) -> None:
    set_trashed(bilingual_snapshot, "item", "IT2ENG22", True)
    item = next(i for i in load(bilingual_snapshot).items if i.key == "IT2ENG22")
    assert item.trashed is True
    set_trashed(bilingual_snapshot, "item", "IT2ENG22", False)
    item = next(i for i in load(bilingual_snapshot).items if i.key == "IT2ENG22")
    assert item.trashed is False


def test_delete_item_removes_children(bilingual_snapshot: Path) -> None:
    delete_item(bilingual_snapshot, "IT2ENG22")
    manifest = load(bilingual_snapshot)
    keys = (
        {i.key for i in manifest.items}
        | {a.key for a in manifest.attachments}
        | {n.key for n in manifest.notes}
        | {a.key for a in manifest.annotations}
    )
    assert keys.isdisjoint({"IT2ENG22", "AT2ENG22", "NT2ENG22", "AN2ENG22"})
    assert not (bilingual_snapshot / "storage" / "AT2ENG22").exists()


def test_make_partial(bilingual_snapshot: Path) -> None:
    make_partial(bilingual_snapshot, "CL2ATTN2")
    manifest = load(bilingual_snapshot)
    assert manifest.completeness == "partial"
    assert counts(manifest) == (2, 2, 2, 1, 2)
