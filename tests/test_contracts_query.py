import re
from typing import Any

import pytest
from pydantic import ValidationError

from hyperwiki.domain.changes import ChangeEvent
from hyperwiki.domain.common import parse_document
from hyperwiki.domain.evidence import EvidenceLocator, make_evidence_id
from hyperwiki.domain.retrieval import RetrievalRequest, RetrievalResult
from hyperwiki.domain.scope import Scope
from hyperwiki.errors import ErrorCode, HyperWikiError

SHA = "a" * 64


def page_locator(**overrides: Any) -> dict[str, Any]:
    return {
        "attachment_key": "AT2ENG22",
        "attachment_sha256": SHA,
        "extractor": "test@1",
        "precision": "page",
        "page_index": 0,
    } | overrides


def change(**overrides: Any) -> dict[str, Any]:
    return {
        "entity": "item",
        "key": "IT2ENG22",
        "change": "trash",
        "version": 3,
        "observed_at": "2026-11-02T00:00:00Z",
    } | overrides


def test_change_event_parses() -> None:
    event = ChangeEvent.model_validate(change())
    assert event.change == "trash"


def test_change_event_rejects_unknown_change() -> None:
    with pytest.raises(ValidationError):
        ChangeEvent.model_validate(change(change="modify"))


def test_page_precision_requires_page_index() -> None:
    with pytest.raises(ValidationError):
        EvidenceLocator.model_validate(page_locator(page_index=None))


def test_span_offsets_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        EvidenceLocator.model_validate(page_locator(precision="span", char_start=10, char_end=5))


def test_span_offsets_must_be_paired() -> None:
    with pytest.raises(ValidationError):
        EvidenceLocator.model_validate(page_locator(precision="span", char_start=10))


def test_evidence_id_is_stable_and_versioned() -> None:
    first = make_evidence_id(EvidenceLocator.model_validate(page_locator()))
    assert first == make_evidence_id(EvidenceLocator.model_validate(page_locator()))
    assert re.fullmatch(r"ev_[0-9a-f]{32}", first)
    assert first != make_evidence_id(
        EvidenceLocator.model_validate(page_locator(extractor="test@2"))
    )
    assert first != make_evidence_id(
        EvidenceLocator.model_validate(page_locator(attachment_sha256="b" * 64))
    )


def test_default_scope_is_whole_library() -> None:
    assert Scope().is_whole_library is True


def test_scope_with_collection_is_not_whole_library() -> None:
    assert Scope(collection_keys=frozenset({"CL2DEEP2"})).is_whole_library is False


def test_year_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        Scope(year_from=2025, year_to=2020)


def test_retrieval_request_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        RetrievalRequest(query="")


def test_retrieval_result_requires_consistent_evidence_ids() -> None:
    locator = page_locator()
    good_id = make_evidence_id(EvidenceLocator.model_validate(locator))
    hit = {
        "evidence_id": good_id,
        "item_key": "IT2ENG22",
        "locator": locator,
        "text": "注意力",
        "score": 1.0,
    }
    data = {
        "schema_version": 1,
        "request": {"query": "attention"},
        "hits": [hit],
        "truncated": False,
    }
    assert parse_document(RetrievalResult, data).hits[0].text == "注意力"
    data["hits"] = [hit | {"evidence_id": "ev_" + "0" * 32}]
    with pytest.raises(HyperWikiError) as caught:
        parse_document(RetrievalResult, data)
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT
