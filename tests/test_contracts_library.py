import json
from typing import Any

import pytest

from hyperwiki.domain.common import parse_document
from hyperwiki.domain.library import LibraryManifest
from hyperwiki.errors import ErrorCode, HyperWikiError
from manifest_factory import minimal_manifest


def assert_invalid(data: dict[str, Any]) -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(LibraryManifest, data)
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT


def test_minimal_manifest_parses() -> None:
    manifest = parse_document(LibraryManifest, minimal_manifest())
    assert manifest.items[0].key == "IT2ENG22"
    assert manifest.annotations[0].parent_key == "AT2ENG22"


def test_naive_datetime_rejected() -> None:
    data = minimal_manifest()
    data["created_at"] = "2026-11-02T00:00:00"
    assert_invalid(data)


def test_duplicate_keys_rejected() -> None:
    data = minimal_manifest()
    data["items"] = data["items"] * 2
    assert_invalid(data)


def test_unknown_attachment_parent_rejected() -> None:
    data = minimal_manifest()
    data["attachments"][0]["parent_key"] = "IT2ZZZ22"
    assert_invalid(data)


def test_standalone_attachment_and_note_allowed() -> None:
    data = minimal_manifest()
    data["attachments"][0]["parent_key"] = None
    data["notes"][0]["parent_key"] = None
    data["annotations"] = []
    manifest = parse_document(LibraryManifest, data)
    assert manifest.attachments[0].parent_key is None
    assert manifest.notes[0].parent_key is None


def test_annotation_parent_must_be_attachment() -> None:
    data = minimal_manifest()
    data["annotations"][0]["parent_key"] = "IT2ENG22"
    assert_invalid(data)


def test_item_collection_must_exist() -> None:
    data = minimal_manifest()
    data["items"][0]["collection_keys"] = ["CL2ZZZZ2"]
    assert_invalid(data)


def test_collection_parent_must_exist() -> None:
    data = minimal_manifest()
    data["collections"][0]["parent_key"] = "CL2ZZZZ2"
    assert_invalid(data)


def test_imported_attachment_requires_path() -> None:
    data = minimal_manifest()
    data["attachments"][0]["path"] = None
    assert_invalid(data)


def test_linked_url_requires_url_and_no_path() -> None:
    data = minimal_manifest()
    data["annotations"] = []
    data["attachments"][0] |= {"link_mode": "linked_url", "path": "x", "url": None}
    assert_invalid(data)
    data["attachments"][0] |= {"path": None, "url": "https://example.org/p"}
    manifest = parse_document(LibraryManifest, data)
    assert manifest.attachments[0].url == "https://example.org/p"


def test_creator_requires_a_name() -> None:
    data = minimal_manifest()
    data["items"][0]["creators"] = [{"creator_type": "author"}]
    assert_invalid(data)


def test_chinese_text_round_trips() -> None:
    data = minimal_manifest()
    data["items"][0]["title"] = "注意力机制综述"
    data["items"][0]["tags"] = ["深度学习"]
    manifest = parse_document(LibraryManifest, data)
    again = parse_document(LibraryManifest, json.loads(manifest.model_dump_json()))
    assert again == manifest
    assert again.items[0].title == "注意力机制综述"
