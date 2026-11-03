"""Evidence services: plan extraction, run it, and read evidence back.

Shared by the CLI now and by the MCP server and update engine later. Planning queues one
``extract.text`` job per current, non-trashed, available attachment version of a supported
type; work already done for the same fingerprint is not repeated unless forced.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime

from pydantic import NonNegativeInt

from hyperwiki.domain.common import Contract
from hyperwiki.domain.evidence import (
    CoverageFailure,
    CoverageState,
    EvidenceCoverage,
    EvidenceRecord,
)
from hyperwiki.domain.work import fingerprint_id
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.processing.evidence_jobs import (
    EXTRACT_TEXT,
    TextExtractionHandler,
    extraction_spec,
)
from hyperwiki.processing.extractors import ExtractionSettings, media_type
from hyperwiki.processing.worker import WorkerReport, new_owner_id, run_worker, utc_now
from hyperwiki.storage.evidence_repo import (
    attachment_states,
    extraction_stats,
    get_stored_evidence,
    version_source,
)
from hyperwiki.storage.job_repo import enqueue, latest_job_states

DEFAULT_SETTINGS = ExtractionSettings()


class ExtractionPlan(Contract):
    """What planning did with each attachment of one library.

    ``already_failed`` counts extractions that failed for the same bytes, extractor, and
    settings; they are not queued again unless retrying failed work or forcing.
    """

    queued: NonNegativeInt
    already_queued: NonNegativeInt
    already_done: NonNegativeInt
    already_failed: NonNegativeInt
    unsupported: NonNegativeInt
    no_file: NonNegativeInt
    trashed: NonNegativeInt


def _never() -> bool:
    return False


def library_ids(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT id FROM libraries ORDER BY name, id")]


def plan_extraction(
    conn: sqlite3.Connection,
    *,
    now: Callable[[], datetime] = utc_now,
    force: bool = False,
    retry_failed: bool = False,
    settings: ExtractionSettings = DEFAULT_SETTINGS,
) -> dict[str, ExtractionPlan]:
    """Queue extraction for every library in the workspace; keyed by library ID."""
    plans: dict[str, ExtractionPlan] = {}
    jobs = latest_job_states(conn, EXTRACT_TEXT)
    for library_id in library_ids(conn):
        counts = dict.fromkeys(ExtractionPlan.model_fields, 0)
        for state in attachment_states(conn, library_id):
            if state.trashed:
                counts["trashed"] += 1
                continue
            if state.version_id is None or state.sha256 is None:
                counts["no_file"] += 1
                continue
            spec = extraction_spec(state.version_id, state.sha256, state.content_type, settings)
            if spec is None:
                counts["unsupported"] += 1
                continue
            if (
                not (force or retry_failed)
                and jobs.get(fingerprint_id(spec), (None,))[0] == "failed"
            ):
                counts["already_failed"] += 1
                continue
            result = enqueue(conn, spec, subject=state.version_id, now=now(), force=force)
            counts[result.outcome] += 1
        plans[library_id] = ExtractionPlan(**counts)
    return plans


def extract_evidence(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    now: Callable[[], datetime] = utc_now,
    max_jobs: int | None = None,
    should_stop: Callable[[], bool] = _never,
) -> WorkerReport:
    """Run queued extraction jobs in this process until none is runnable or a limit is hit."""
    return run_worker(
        conn,
        {EXTRACT_TEXT: TextExtractionHandler(conn, now=now)},
        owner=owner or new_owner_id(),
        now=now,
        max_jobs=max_jobs,
        should_stop=should_stop,
    )


def read_evidence(
    conn: sqlite3.Connection, evidence_id: str, *, settings: ExtractionSettings = DEFAULT_SETTINGS
) -> EvidenceRecord:
    """Return one evidence span; ``current`` is judged against ``settings``. Never writes."""
    stored = get_stored_evidence(conn, evidence_id)
    source = None if stored is None else version_source(conn, stored.attachment_version_id)
    if stored is None or source is None or stored.spec.extractor is None:
        raise HyperWikiError(ErrorCode.NOT_FOUND, "no such evidence", {"evidence_id": evidence_id})
    expected = extraction_spec(source.version_id, source.sha256, source.content_type, settings)
    current = (
        stored.version_is_current
        and expected is not None
        and fingerprint_id(expected) == stored.extraction_id
    )
    return EvidenceRecord(
        id=stored.id,
        text=stored.text,
        locator=stored.locator,
        library_id=stored.library_id,
        item_key=stored.item_key,
        attachment_key=stored.attachment_key,
        attachment_version_id=stored.attachment_version_id,
        extractor=stored.spec.extractor,
        current=current,
    )


def evidence_coverage(
    conn: sqlite3.Connection, library_id: str, *, settings: ExtractionSettings = DEFAULT_SETTINGS
) -> EvidenceCoverage:
    """Each attachment's evidence state under ``settings``, with page and evidence counts."""
    stats = extraction_stats(conn, library_id)
    jobs = latest_job_states(conn, EXTRACT_TEXT)
    states: dict[CoverageState, int] = {
        "extracted": 0,
        "queued": 0,
        "failed": 0,
        "pending": 0,
        "unsupported": 0,
        "no_file": 0,
    }
    unsupported_types: dict[str, int] = {}
    failures: list[CoverageFailure] = []
    trashed = pages = pages_without_text = attachments_without_text = evidence = 0
    for attachment in attachment_states(conn, library_id):
        if attachment.trashed:
            trashed += 1
            continue
        if attachment.version_id is None or attachment.sha256 is None:
            states["no_file"] += 1
            continue
        spec = extraction_spec(
            attachment.version_id, attachment.sha256, attachment.content_type, settings
        )
        if spec is None:
            states["unsupported"] += 1
            kind = media_type(attachment.content_type) or "unknown"
            unsupported_types[kind] = unsupported_types.get(kind, 0) + 1
            continue
        fingerprint = fingerprint_id(spec)
        extracted = stats.get(fingerprint)
        if extracted is not None:
            states["extracted"] += 1
            pages += extracted.pages
            pages_without_text += extracted.pages_without_text
            evidence += extracted.evidence
            if extracted.pages_without_text == extracted.pages:
                attachments_without_text += 1
            continue
        job_state, error = jobs.get(fingerprint, (None, None))
        if job_state in ("queued", "leased"):
            states["queued"] += 1
        elif job_state == "failed":
            states["failed"] += 1
            failures.append(
                CoverageFailure(
                    attachment_key=attachment.key,
                    code=ErrorCode.JOB_FAILED if error is None else error.code,
                    message="" if error is None else error.message,
                )
            )
        else:
            states["pending"] += 1
    return EvidenceCoverage(
        attachments=sum(states.values()),
        states=states,
        trashed=trashed,
        pages=pages,
        pages_without_text=pages_without_text,
        attachments_without_text=attachments_without_text,
        evidence=evidence,
        unsupported_types=dict(sorted(unsupported_types.items())),
        failures=failures,
    )
