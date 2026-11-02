from typing import Any

import pytest

from hyperwiki.domain.common import parse_document
from hyperwiki.domain.wiki import WikiFrontmatter
from hyperwiki.errors import ErrorCode, HyperWikiError


def paper_page(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "3f2b9c1e-0000-4000-8000-000000000001",
        "page_type": "paper",
        "title": "基于注意力机制的合成实验研究",
        "language": "zh",
        "source_item_keys": ["IT2ZHN22"],
        "evidence_ids": ["ev_" + "1" * 32],
        "revision": 1,
        "status": "current",
        "generated_by": {
            "provider_kind": "local",
            "model": "qwen3:8b",
            "prompt_version": "paper-v1",
        },
        "generated_sha256": "c" * 64,
        "last_verified_at": "2026-11-02T00:00:00Z",
    } | overrides


def assert_invalid(data: dict[str, Any]) -> None:
    with pytest.raises(HyperWikiError) as caught:
        parse_document(WikiFrontmatter, data)
    assert caught.value.code is ErrorCode.INVALID_DOCUMENT


def test_generated_paper_page_parses() -> None:
    page = parse_document(WikiFrontmatter, paper_page())
    assert page.generated_by is not None and page.generated_by.provider_kind == "local"


def test_personal_note_cannot_have_generator() -> None:
    assert_invalid(paper_page(page_type="note"))


def test_personal_note_without_generator_parses() -> None:
    page = parse_document(
        WikiFrontmatter, paper_page(page_type="note", generated_by=None, generated_sha256=None)
    )
    assert page.page_type == "note"


def test_current_generated_page_requires_fingerprint_and_hash() -> None:
    assert_invalid(paper_page(generated_by=None))


def test_pending_page_may_lack_generator() -> None:
    page = parse_document(
        WikiFrontmatter, paper_page(status="pending", generated_by=None, generated_sha256=None)
    )
    assert page.status == "pending"


def test_language_is_restricted() -> None:
    assert_invalid(paper_page(language="fr"))


def test_revision_starts_at_one() -> None:
    assert_invalid(paper_page(revision=0))
