"""Fixture locations and helpers that simulate Zotero library changes on a snapshot copy.

Only use these on a copy (see the ``bilingual_snapshot`` fixture), never on the checked-in
fixtures.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Literal

FIXTURES_DIR = Path(__file__).parent / "fixtures"
BILINGUAL_DIR = FIXTURES_DIR / "libraries" / "bilingual"

MUTATION_TIME = "2026-11-03T00:00:00Z"
ENTITY_LISTS = ("collections", "items", "attachments", "notes", "annotations")
_LIST_FOR = {"item": "items", "attachment": "attachments", "note": "notes"}


def load_manifest_dict(snapshot_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    return data


def write_manifest_dict(snapshot_dir: Path, data: dict[str, Any]) -> None:
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def _find(data: dict[str, Any], list_name: str, key: str) -> dict[str, Any]:
    for entity in data[list_name]:
        if entity["key"] == key:
            found: dict[str, Any] = entity
            return found
    raise KeyError(f"{key} not found in {list_name}")


def _touch(data: dict[str, Any], entity: dict[str, Any]) -> None:
    latest = max(e["version"] for name in ENTITY_LISTS for e in data[name])
    entity["version"] = latest + 1
    entity["date_modified"] = MUTATION_TIME


def edit_item_title(snapshot_dir: Path, key: str, title: str) -> None:
    data = load_manifest_dict(snapshot_dir)
    item = _find(data, "items", key)
    item["title"] = title
    _touch(data, item)
    write_manifest_dict(snapshot_dir, data)


def replace_attachment_content(snapshot_dir: Path, key: str, content: bytes) -> None:
    data = load_manifest_dict(snapshot_dir)
    attachment = _find(data, "attachments", key)
    (snapshot_dir / data["storage_root"] / attachment["path"]).write_bytes(content)
    _touch(data, attachment)
    write_manifest_dict(snapshot_dir, data)


def set_trashed(
    snapshot_dir: Path,
    entity: Literal["item", "attachment", "note"],
    key: str,
    trashed: bool,
) -> None:
    data = load_manifest_dict(snapshot_dir)
    record = _find(data, _LIST_FOR[entity], key)
    record["trashed"] = trashed
    _touch(data, record)
    write_manifest_dict(snapshot_dir, data)


def delete_item(snapshot_dir: Path, key: str) -> None:
    """Permanently delete an item with its attachments, notes, annotations, and files."""
    data = load_manifest_dict(snapshot_dir)
    _find(data, "items", key)
    removed_attachments = {a["key"] for a in data["attachments"] if a["parent_key"] == key}
    data["items"] = [i for i in data["items"] if i["key"] != key]
    data["attachments"] = [a for a in data["attachments"] if a["key"] not in removed_attachments]
    data["notes"] = [n for n in data["notes"] if n["parent_key"] != key]
    data["annotations"] = [
        a for a in data["annotations"] if a["parent_key"] not in removed_attachments
    ]
    write_manifest_dict(snapshot_dir, data)
    for attachment_key in removed_attachments:
        shutil.rmtree(snapshot_dir / data["storage_root"] / attachment_key, ignore_errors=True)


def make_partial(snapshot_dir: Path, collection_key: str) -> None:
    """Reduce the snapshot to one collection's items, as a partial export would."""
    data = load_manifest_dict(snapshot_dir)
    items = [i for i in data["items"] if collection_key in i["collection_keys"]]
    item_keys = {i["key"] for i in items}
    attachments = [a for a in data["attachments"] if a["parent_key"] in item_keys]
    attachment_keys = {a["key"] for a in attachments}

    parents = {c["key"]: c["parent_key"] for c in data["collections"]}
    kept_collections: set[str] = set()
    for item in items:
        for key in item["collection_keys"]:
            current: str | None = key
            while current is not None and current not in kept_collections:
                kept_collections.add(current)
                current = parents[current]

    data["collections"] = [c for c in data["collections"] if c["key"] in kept_collections]
    data["items"] = items
    data["attachments"] = attachments
    data["notes"] = [n for n in data["notes"] if n["parent_key"] in item_keys]
    data["annotations"] = [a for a in data["annotations"] if a["parent_key"] in attachment_keys]
    data["completeness"] = "partial"
    write_manifest_dict(snapshot_dir, data)
