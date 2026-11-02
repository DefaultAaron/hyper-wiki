from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from hyperwiki.domain.common import VersionedDocument, ZoteroKey, parse_document
from hyperwiki.errors import ErrorCode, HyperWikiError


class Example(VersionedDocument):
    name: str


def test_parse_document_accepts_supported_version() -> None:
    assert parse_document(Example, {"schema_version": 1, "name": "x"}).name == "x"


@pytest.mark.parametrize("version", [2, "1", None])
def test_parse_document_rejects_unsupported_version(version: object) -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, {"schema_version": version, "name": "x"})
    assert caught.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION
    assert caught.value.details == {"schema_version": version, "supported": [1]}


def test_parse_document_rejects_missing_version() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, {"name": "x"})
    assert caught.value.details == {"schema_version": None, "supported": [1]}


def test_parse_document_rejects_unknown_field() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, {"schema_version": 1, "name": "x", "surprise": True})
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
    errors: list[dict[str, Any]] = caught.value.details["errors"]
    assert errors[0]["loc"] == ["surprise"]


def test_parse_document_rejects_non_mapping() -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(Example, ["not", "a", "mapping"])
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT


def test_error_info_is_json_ready() -> None:
    error = HyperWikiError(ErrorCode.NOT_FOUND, "missing", {"path": "x"})
    assert str(error) == "not_found: missing"
    assert error.to_info().model_dump(mode="json") == {
        "code": "not_found",
        "message": "missing",
        "details": {"path": "x"},
    }


@pytest.mark.parametrize("key", ["ABCD2345", "IT2ENG22"])
def test_zotero_key_accepts_valid(key: str) -> None:
    assert TypeAdapter(ZoteroKey).validate_python(key) == key


@pytest.mark.parametrize("key", ["abcd2345", "ABCD1234", "ABCDO234", "ABC", "ABCD23456"])
def test_zotero_key_rejects_invalid(key: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ZoteroKey).validate_python(key)
