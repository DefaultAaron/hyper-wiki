import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperwiki.domain.evidence import BoundingBox, make_evidence_id
from hyperwiki.domain.extraction import ExtractedDocument, ExtractedPage, ExtractedSpan
from hyperwiki.domain.work import Component, WorkFingerprint, fingerprint_id
from hyperwiki.processing.extractors import ExtractionSettings, extract_plaintext
from hyperwiki.services.library_import import import_snapshot
from hyperwiki.storage.evidence_repo import (
    VersionSource,
    attachment_states,
    get_stored_evidence,
    replace_extraction,
    version_source,
)
from hyperwiki.storage.library_repo import current_attachment_version, stable_id

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _source(conn: sqlite3.Connection, library_id: str, key: str) -> VersionSource:
    version = current_attachment_version(conn, stable_id(library_id, "attachment", key))
    assert version is not None
    source = version_source(conn, version.id)
    assert source is not None
    return source


def _spec(source: VersionSource) -> WorkFingerprint:
    return WorkFingerprint(
        operation="extract.text",
        operation_version=1,
        inputs={"attachment_version": source.version_id, "sha256": source.sha256},
        extractor=Component(name="hyperwiki.plaintext", version="1"),
        schema_version=1,
    )


def _document(source: VersionSource) -> ExtractedDocument:
    assert source.file_path is not None
    return extract_plaintext(Path(source.file_path).read_bytes(), ExtractionSettings())


def _evidence_ids(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT id FROM evidence ORDER BY ordinal")]


def test_import_stores_resolved_file_path(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    paths = dict(database.execute("SELECT key, file_path FROM attachments").fetchall())
    expected = (bilingual_snapshot / "storage" / "AT2ENG22" / "attention-study.txt").resolve()
    assert paths["AT2ENG22"] == str(expected)
    assert paths["AT2MIS22"] is None and paths["AT2URL22"] is None
    source = _source(database, library_id, "AT2ZHN22")
    assert (source.attachment_key, source.item_key, source.content_type) == (
        "AT2ZHN22",
        "IT2ZHN22",
        "text/plain",
    )
    assert source.current and not source.trashed and source.availability == "available"


def test_attachment_states_list_every_attachment(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    states = {state.key: state for state in attachment_states(database, library_id)}
    assert sorted(states) == ["AT2ENG22", "AT2MIS22", "AT2URL22", "AT2ZHN22"]
    assert states["AT2ENG22"].version_id is not None
    assert states["AT2MIS22"].version_id is None
    assert states["AT2MIS22"].availability == "missing"
    assert states["AT2URL22"].content_type == "text/html"


def test_replace_extraction_round_trips_evidence(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    source = _source(database, library_id, "AT2ZHN22")
    spec = _spec(source)
    document = _document(source)
    with database:
        summary = replace_extraction(database, spec=spec, source=source, document=document, now=NOW)
    assert (summary.extraction_id, summary.pages, summary.pages_without_text) == (
        fingerprint_id(spec),
        2,
        0,
    )
    assert summary.evidence == len(_evidence_ids(database)) == 2

    second_page = document.pages[1]
    span = second_page.spans[0]
    stored = [get_stored_evidence(database, evidence_id) for evidence_id in _evidence_ids(database)]
    last = stored[1]
    assert last is not None
    assert last.text == second_page.text[span.char_start : span.char_end]
    assert last.text.startswith("在该模型中")
    assert last.locator.page_index == 1
    assert last.locator.extractor == fingerprint_id(spec)
    assert last.locator.attachment_sha256 == source.sha256
    assert (last.locator.char_start, last.locator.char_end) == (span.char_start, span.char_end)
    assert last.id == make_evidence_id(last.locator)
    assert (last.item_key, last.attachment_key, last.attachment_version_id) == (
        "IT2ZHN22",
        "AT2ZHN22",
        source.version_id,
    )
    assert last.version_is_current and last.spec == spec
    assert get_stored_evidence(database, "ev_" + "0" * 32) is None


def test_replace_extraction_is_idempotent(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    source = _source(database, library_id, "AT2ENG22")
    for _ in range(2):
        with database:
            replace_extraction(
                database, spec=_spec(source), source=source, document=_document(source), now=NOW
            )
    first = _evidence_ids(database)
    with database:
        replace_extraction(
            database, spec=_spec(source), source=source, document=_document(source), now=NOW
        )
    assert _evidence_ids(database) == first
    assert database.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 1


def test_boxes_are_rounded_to_hundredths(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    source = _source(database, library_id, "AT2ENG22")
    box = BoundingBox(x0=72.123456, y0=700.004999, x1=300.0, y1=767.199999)
    page = ExtractedPage(
        index=0,
        label="3",
        width=612.0,
        height=792.0,
        text="Some evidence text on a page.",
        has_text=True,
        spans=[ExtractedSpan(char_start=5, char_end=13, bbox=box)],
    )
    with database:
        replace_extraction(
            database,
            spec=_spec(source),
            source=source,
            document=ExtractedDocument(pages=[page]),
            now=NOW,
        )
    stored = get_stored_evidence(database, _evidence_ids(database)[0])
    assert stored is not None and stored.text == "evidence"
    assert stored.locator.bbox == BoundingBox(x0=72.12, y0=700.0, x1=300.0, y1=767.2)
    assert stored.locator.page_label == "3"


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Some evidence text on a page.", 0, 99),
        ("Some evidence text on a page.", 40, 50),
        ("Some   evidence", 4, 7),
    ],
    ids=["past-end", "outside", "whitespace-only"],
)
def test_span_outside_page_text_is_rejected(
    database: sqlite3.Connection, bilingual_snapshot: Path, text: str, start: int, end: int
) -> None:
    library_id = import_snapshot(database, bilingual_snapshot).library_id
    source = _source(database, library_id, "AT2ENG22")
    page = ExtractedPage(
        index=0,
        label=None,
        width=None,
        height=None,
        text=text,
        has_text=True,
        spans=[ExtractedSpan(char_start=start, char_end=end)],
    )
    with pytest.raises(ValueError, match="span"), database:
        replace_extraction(
            database,
            spec=_spec(source),
            source=source,
            document=ExtractedDocument(pages=[page]),
            now=NOW,
        )
    assert database.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 0
