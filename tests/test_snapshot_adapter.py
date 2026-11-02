import json
import os
from pathlib import Path

import pytest

from hyperwiki.adapters.snapshot import load_snapshot
from hyperwiki.errors import ErrorCode, HyperWikiError
from snapshot_mutations import load_manifest_dict, write_manifest_dict


def point_attachment(snapshot_dir: Path, key: str, **fields: object) -> None:
    data = load_manifest_dict(snapshot_dir)
    attachment = next(a for a in data["attachments"] if a["key"] == key)
    attachment.update(fields)
    write_manifest_dict(snapshot_dir, data)


def test_loads_bilingual_fixture(bilingual_snapshot: Path) -> None:
    loaded = load_snapshot(bilingual_snapshot)
    checksums = json.loads((bilingual_snapshot / "checksums.json").read_text(encoding="utf-8"))
    assert loaded.files["AT2ENG22"].availability == "available"
    assert loaded.files["AT2ENG22"].sha256 == checksums["AT2ENG22/attention-study.txt"]
    assert loaded.files["AT2MIS22"].availability == "missing"
    assert loaded.files["AT2URL22"].availability == "remote"
    assert [(p.code, p.entity, p.key) for p in loaded.problems] == [
        (ErrorCode.NOT_FOUND, "attachment", "AT2MIS22")
    ]


def test_chinese_filename_resolves(bilingual_snapshot: Path) -> None:
    loaded = load_snapshot(bilingual_snapshot)
    checksums = json.loads((bilingual_snapshot / "checksums.json").read_text(encoding="utf-8"))
    assert loaded.files["AT2ZHN22"].availability == "available"
    assert loaded.files["AT2ZHN22"].sha256 == checksums["AT2ZHN22/中文论文.txt"]


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "AT2ENG22\\attention-study.txt",
        "AT2ENG22/../../outside.txt",
    ],
)
def test_path_escape_is_reported_not_read(bilingual_snapshot: Path, bad_path: str) -> None:
    point_attachment(bilingual_snapshot, "AT2ENG22", path=bad_path)
    loaded = load_snapshot(bilingual_snapshot)
    assert loaded.files["AT2ENG22"].availability == "invalid_path"
    assert loaded.files["AT2ENG22"].sha256 is None
    assert (ErrorCode.INVALID_PATH, "AT2ENG22") in [(p.code, p.key) for p in loaded.problems]


def test_symlink_escaping_storage_is_invalid(bilingual_snapshot: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = bilingual_snapshot / "storage" / "AT2ENG22" / "link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("creating symlinks is not permitted here")
    point_attachment(bilingual_snapshot, "AT2ENG22", path="AT2ENG22/link.txt")
    loaded = load_snapshot(bilingual_snapshot)
    assert loaded.files["AT2ENG22"].availability == "invalid_path"
    assert loaded.files["AT2ENG22"].sha256 is None


def test_linked_file_roots(bilingual_snapshot: Path, tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    paper = papers / "linked.txt"
    paper.write_text("linked paper", encoding="utf-8")
    point_attachment(bilingual_snapshot, "AT2ENG22", link_mode="linked_file", path=str(paper))
    assert load_snapshot(bilingual_snapshot).files["AT2ENG22"].availability == "invalid_path"
    loaded = load_snapshot(bilingual_snapshot, linked_file_roots=[papers])
    assert loaded.files["AT2ENG22"].availability == "available"
    assert loaded.files["AT2ENG22"].size_bytes == len(b"linked paper")


def test_missing_manifest_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(HyperWikiError) as caught:
        load_snapshot(tmp_path)
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_malformed_json_is_invalid_document(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(HyperWikiError) as caught:
        load_snapshot(tmp_path)
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    assert "line" in caught.value.details


def test_storage_root_must_stay_inside_snapshot(bilingual_snapshot: Path) -> None:
    data = load_manifest_dict(bilingual_snapshot)
    data["storage_root"] = "../elsewhere"
    write_manifest_dict(bilingual_snapshot, data)
    with pytest.raises(HyperWikiError) as caught:
        load_snapshot(bilingual_snapshot)
    assert caught.value.code is ErrorCode.INVALID_PATH


def test_load_does_not_modify_snapshot_directory(bilingual_snapshot: Path) -> None:
    def state() -> list[tuple[str, int, int]]:
        return sorted(
            (p.relative_to(bilingual_snapshot).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
            for p in bilingual_snapshot.rglob("*")
        )

    before = state()
    load_snapshot(bilingual_snapshot)
    assert state() == before
