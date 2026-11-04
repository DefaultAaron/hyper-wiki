"""Repository for extraction artifacts: page text and evidence spans, keyed by fingerprint."""

import json
import sqlite3
from datetime import datetime

from hyperwiki.domain.common import Contract, Sha256Hex, ZoteroKey
from hyperwiki.domain.evidence import BoundingBox, EvidenceId, EvidenceLocator, make_evidence_id
from hyperwiki.domain.extraction import ExtractedDocument
from hyperwiki.domain.library import Availability
from hyperwiki.domain.work import WorkFingerprint, fingerprint_id
from hyperwiki.storage.database import to_db_time

BBOX_DECIMALS = 2


class VersionSource(Contract):
    """An attachment version with what extraction needs to read it.

    ``trashed`` and ``deleted`` (tombstoned) hold when the attachment or its parent item is.
    """

    version_id: str
    attachment_id: str
    attachment_key: ZoteroKey
    library_id: str
    item_key: ZoteroKey | None
    sha256: Sha256Hex
    size_bytes: int
    content_type: str
    file_path: str | None
    current: bool
    trashed: bool
    deleted: bool
    item_id: str | None
    availability: Availability


class AttachmentState(Contract):
    """A live attachment and its current version, if its file is available.

    ``trashed`` holds when the attachment or its parent item is in the trash.
    """

    attachment_id: str
    item_id: str | None
    key: ZoteroKey
    content_type: str
    trashed: bool
    availability: Availability
    version_id: str | None
    sha256: Sha256Hex | None


class ExtractionSummary(Contract):
    extraction_id: str
    pages: int
    pages_without_text: int
    evidence: int


class StoredEvidence(Contract):
    id: EvidenceId
    text: str
    locator: EvidenceLocator
    library_id: str
    item_key: ZoteroKey | None
    attachment_key: ZoteroKey
    attachment_version_id: str
    version_is_current: bool
    extraction_id: str
    spec: WorkFingerprint


_SOURCE_COLUMNS = """
    v.id AS version_id, v.sha256 AS version_sha256, v.size_bytes AS version_size,
    a.id AS attachment_id, a.key AS attachment_key, a.library_id, a.content_type,
    a.file_path, a.sha256 AS current_sha256, a.availability, i.id AS item_id, i.key AS item_key,
    a.trashed OR COALESCE(i.trashed, 0) AS trashed,
    a.deleted_at IS NOT NULL OR i.deleted_at IS NOT NULL AS deleted
"""


def version_source(conn: sqlite3.Connection, version_id: str) -> VersionSource | None:
    row = conn.execute(
        f"""
        SELECT {_SOURCE_COLUMNS}
        FROM attachment_versions AS v
        JOIN attachments AS a ON a.id = v.attachment_id
        LEFT JOIN items AS i ON i.id = a.parent_item_id
        WHERE v.id = ?
        """,
        (version_id,),
    ).fetchone()
    if row is None:
        return None
    return VersionSource(
        version_id=row["version_id"],
        attachment_id=row["attachment_id"],
        attachment_key=row["attachment_key"],
        library_id=row["library_id"],
        item_key=row["item_key"],
        sha256=row["version_sha256"],
        size_bytes=row["version_size"],
        content_type=row["content_type"],
        file_path=row["file_path"],
        current=row["current_sha256"] == row["version_sha256"],
        trashed=bool(row["trashed"]),
        deleted=bool(row["deleted"]),
        item_id=row["item_id"],
        availability=row["availability"],
    )


def attachment_states(conn: sqlite3.Connection, library_id: str) -> list[AttachmentState]:
    """Every live attachment of a library; tombstoned ones, or children of tombstoned items,
    are left out."""
    rows = conn.execute(
        """
        SELECT a.id, a.parent_item_id, a.key, a.content_type, a.availability,
            a.trashed OR COALESCE(i.trashed, 0) AS trashed,
            v.id AS version_id, v.sha256
        FROM attachments AS a
        LEFT JOIN items AS i ON i.id = a.parent_item_id
        LEFT JOIN attachment_versions AS v
            ON v.attachment_id = a.id AND v.sha256 = a.sha256 AND a.availability = 'available'
        WHERE a.library_id = ? AND a.deleted_at IS NULL AND i.deleted_at IS NULL
        ORDER BY a.key
        """,
        (library_id,),
    )
    return [
        AttachmentState(
            attachment_id=row["id"],
            item_id=row["parent_item_id"],
            key=row["key"],
            content_type=row["content_type"],
            trashed=bool(row["trashed"]),
            availability=row["availability"],
            version_id=row["version_id"],
            sha256=row["sha256"],
        )
        for row in rows
    ]


def queued_version_jobs(
    conn: sqlite3.Connection, library_id: str, operation: str
) -> list[tuple[str, str]]:
    """(job ID, fingerprint) of queued jobs of ``operation`` on versions of the library's
    attachments."""
    rows = conn.execute(
        """
        SELECT j.id, j.fingerprint FROM jobs AS j
        JOIN attachment_versions AS v ON v.id = j.subject
        JOIN attachments AS a ON a.id = v.attachment_id
        WHERE j.operation = ? AND j.state = 'queued' AND a.library_id = ?
        ORDER BY j.created_at, j.rowid
        """,
        (operation, library_id),
    )
    return [(row[0], row[1]) for row in rows]


def _rounded(box: BoundingBox | None) -> BoundingBox | None:
    if box is None:
        return None
    return BoundingBox(
        x0=round(box.x0, BBOX_DECIMALS),
        y0=round(box.y0, BBOX_DECIMALS),
        x1=round(box.x1, BBOX_DECIMALS),
        y1=round(box.y1, BBOX_DECIMALS),
    )


def delete_extraction(conn: sqlite3.Connection, extraction_id: str) -> None:
    conn.execute("DELETE FROM evidence WHERE extraction_id = ?", (extraction_id,))
    conn.execute("DELETE FROM extraction_pages WHERE extraction_id = ?", (extraction_id,))
    conn.execute("DELETE FROM extractions WHERE id = ?", (extraction_id,))


def replace_extraction(
    conn: sqlite3.Connection,
    *,
    spec: WorkFingerprint,
    source: VersionSource,
    document: ExtractedDocument,
    now: datetime,
) -> ExtractionSummary:
    """Store an extraction's pages and evidence, replacing an earlier run of the same work.

    Runs inside the caller's transaction. Raises ``ValueError`` before writing anything if a
    span does not lie within its page's text or covers only whitespace.
    """
    for page in document.pages:
        for span in page.spans:
            quote = page.text[span.char_start : span.char_end]
            if span.char_end > len(page.text) or not quote.strip():
                raise ValueError(
                    f"span {span.char_start}-{span.char_end} on page {page.index} does not "
                    "cover text of that page"
                )
    extraction_id = fingerprint_id(spec)
    delete_extraction(conn, extraction_id)
    conn.execute(
        """
        INSERT INTO extractions
            (id, attachment_version_id, fingerprint_json, page_count, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            extraction_id,
            source.version_id,
            spec.model_dump_json(),
            len(document.pages),
            to_db_time(now),
        ),
    )
    ordinal = 0
    for page in document.pages:
        conn.execute(
            """
            INSERT INTO extraction_pages
                (extraction_id, page_index, page_label, width, height, text, has_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extraction_id,
                page.index,
                page.label,
                page.width,
                page.height,
                page.text,
                int(page.has_text),
            ),
        )
        for span in page.spans:
            locator = EvidenceLocator(
                attachment_key=source.attachment_key,
                attachment_sha256=source.sha256,
                extractor=extraction_id,
                precision="span",
                page_index=page.index,
                page_label=page.label,
                char_start=span.char_start,
                char_end=span.char_end,
                bbox=_rounded(span.bbox),
            )
            conn.execute(
                """
                INSERT INTO evidence (id, extraction_id, attachment_id, page_index, ordinal,
                    char_start, char_end, text, locator_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_evidence_id(locator),
                    extraction_id,
                    source.attachment_id,
                    page.index,
                    ordinal,
                    span.char_start,
                    span.char_end,
                    page.text[span.char_start : span.char_end],
                    locator.model_dump_json(),
                ),
            )
            ordinal += 1
    return ExtractionSummary(
        extraction_id=extraction_id,
        pages=len(document.pages),
        pages_without_text=sum(1 for page in document.pages if not page.has_text),
        evidence=ordinal,
    )


def get_stored_evidence(conn: sqlite3.Connection, evidence_id: str) -> StoredEvidence | None:
    row = conn.execute(
        """
        SELECT e.id, e.text, e.locator_json, e.extraction_id, x.fingerprint_json,
            x.attachment_version_id, v.sha256 AS version_sha256, a.sha256 AS current_sha256,
            a.key AS attachment_key, a.library_id, i.key AS item_key
        FROM evidence AS e
        JOIN extractions AS x ON x.id = e.extraction_id
        JOIN attachment_versions AS v ON v.id = x.attachment_version_id
        JOIN attachments AS a ON a.id = e.attachment_id
        LEFT JOIN items AS i ON i.id = a.parent_item_id
        WHERE e.id = ?
        """,
        (evidence_id,),
    ).fetchone()
    if row is None:
        return None
    return StoredEvidence(
        id=row["id"],
        text=row["text"],
        locator=EvidenceLocator.model_validate(json.loads(row["locator_json"])),
        library_id=row["library_id"],
        item_key=row["item_key"],
        attachment_key=row["attachment_key"],
        attachment_version_id=row["attachment_version_id"],
        version_is_current=row["version_sha256"] == row["current_sha256"],
        extraction_id=row["extraction_id"],
        spec=WorkFingerprint.model_validate_json(row["fingerprint_json"]),
    )


class ExtractionStats(Contract):
    pages: int
    pages_without_text: int
    evidence: int


def extraction_stats(conn: sqlite3.Connection, library_id: str) -> dict[str, ExtractionStats]:
    """Page and evidence counts of every stored extraction of a library, keyed by its ID."""
    rows = conn.execute(
        """
        SELECT x.id, x.page_count,
            (SELECT COUNT(*) FROM extraction_pages AS p
             WHERE p.extraction_id = x.id AND p.has_text = 0) AS textless,
            (SELECT COUNT(*) FROM evidence AS e WHERE e.extraction_id = x.id) AS spans
        FROM extractions AS x
        JOIN attachment_versions AS v ON v.id = x.attachment_version_id
        JOIN attachments AS a ON a.id = v.attachment_id
        WHERE a.library_id = ?
        """,
        (library_id,),
    )
    return {
        row["id"]: ExtractionStats(
            pages=row["page_count"], pages_without_text=row["textless"], evidence=row["spans"]
        )
        for row in rows
    }
