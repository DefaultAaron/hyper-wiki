"""The ``extract.text`` job: turn one attachment version into pages and evidence spans.

The job's fingerprint names the attachment version, its SHA-256, the extractor and its
versions, the cleanup and chunker versions, and the settings. The handler reads the file
once, checks its hash, extracts in memory, and stores the result in the completion
transaction, so evidence always belongs to the exact bytes and code named by its fingerprint.
"""

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from hyperwiki.domain.common import Contract
from hyperwiki.domain.extraction import ExtractedDocument
from hyperwiki.domain.work import Job, WorkFingerprint
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.processing.chunking import CHUNKER_VERSION
from hyperwiki.processing.extractors import (
    PDF_TYPE,
    ExtractionSettings,
    extract_pdf,
    extract_plaintext,
    extractor_component,
    media_type,
)
from hyperwiki.processing.text_cleanup import CLEANUP_VERSION
from hyperwiki.processing.worker import JobContext, JobFailure, utc_now
from hyperwiki.storage.evidence_repo import VersionSource, replace_extraction, version_source

EXTRACT_TEXT = "extract.text"
OPERATION_VERSION = 1
SCHEMA_VERSION = 1
HEARTBEAT_PAGES = 25


def extraction_spec(
    version_id: str, sha256: str, content_type: str, settings: ExtractionSettings
) -> WorkFingerprint | None:
    """The fingerprint of extracting this attachment version, or None if unsupported."""
    component = extractor_component(content_type)
    if component is None:
        return None
    return WorkFingerprint(
        operation=EXTRACT_TEXT,
        operation_version=OPERATION_VERSION,
        inputs={"attachment_version": version_id, "sha256": sha256},
        extractor=component,
        schema_version=SCHEMA_VERSION,
        config={
            "cleanup_version": CLEANUP_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "chunk_chars": settings.chunk_chars,
            "min_page_chars": settings.min_page_chars,
        },
    )


def _settings_from(spec: WorkFingerprint) -> ExtractionSettings | None:
    chunk_chars = spec.config.get("chunk_chars")
    min_page_chars = spec.config.get("min_page_chars")
    if type(chunk_chars) is not int or type(min_page_chars) is not int:
        return None
    try:
        return ExtractionSettings(chunk_chars=chunk_chars, min_page_chars=min_page_chars)
    except ValueError:
        return None


class ExtractionOutput(Contract):
    source: VersionSource
    document: ExtractedDocument


def _fail(code: ErrorCode, message: str, **details: str) -> JobFailure:
    return JobFailure(ErrorInfo(code=code, message=message, details=details), retryable=False)


class TextExtractionHandler:
    """Runs ``extract.text`` jobs; reads its inputs through ``conn`` and never writes there
    outside ``store``."""

    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], datetime] = utc_now) -> None:
        self._conn = conn
        self._now = now

    def execute(self, job: Job, context: JobContext) -> ExtractionOutput:
        spec = job.spec
        source = version_source(self._conn, spec.inputs.get("attachment_version", ""))
        if source is None:
            raise _fail(ErrorCode.ATTACHMENT_CHANGED, "attachment version is no longer stored")
        settings = _settings_from(spec)
        if settings is None or spec != extraction_spec(
            source.version_id, source.sha256, source.content_type, settings
        ):
            raise _fail(
                ErrorCode.JOB_FAILED,
                "the extractor or its settings changed since this job was queued",
            )
        if source.file_path is None:
            raise _fail(ErrorCode.ATTACHMENT_CHANGED, "attachment file is not available")
        try:
            data = Path(source.file_path).read_bytes()
        except OSError as error:
            raise _fail(
                ErrorCode.ATTACHMENT_CHANGED,
                f"attachment file cannot be read: {error.strerror or error}",
            ) from error
        if hashlib.sha256(data).hexdigest() != source.sha256:
            raise _fail(
                ErrorCode.ATTACHMENT_CHANGED,
                "attachment file changed since it was imported; sync again",
            )

        def on_page(index: int) -> None:
            if (index + 1) % HEARTBEAT_PAGES == 0:
                context.heartbeat()

        try:
            if media_type(source.content_type) == PDF_TYPE:
                document = extract_pdf(data, settings, on_page=on_page)
            else:
                document = extract_plaintext(data, settings)
        except HyperWikiError as error:
            raise JobFailure(error.to_info(), retryable=False) from error
        return ExtractionOutput(source=source, document=document)

    def store(
        self, conn: sqlite3.Connection, job: Job, result: ExtractionOutput
    ) -> Mapping[str, JsonValue]:
        summary = replace_extraction(
            conn, spec=job.spec, source=result.source, document=result.document, now=self._now()
        )
        return summary.model_dump(mode="json", exclude={"extraction_id"})
