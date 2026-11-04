"""Evidence services: plan extraction, run it, and read evidence back.

Shared by the CLI, the update engine, and later the MCP server. Planning queues one
``extract.text`` job per current version of each live attachment that is not trashed, not
excluded, available, and of a supported type; work already done for the same fingerprint is
not repeated unless forced. Planning the whole workspace also cancels queued jobs that are no
longer wanted.
"""

import sqlite3
from collections.abc import Callable, Collection
from datetime import datetime

from pydantic import NonNegativeInt

from hyperwiki.domain.common import Contract
from hyperwiki.domain.evidence import (
    CoverageFailure,
    CoverageState,
    EvidenceCoverage,
    EvidenceRecord,
    EvidenceStatus,
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
    queued_version_jobs,
    version_source,
)
from hyperwiki.storage.job_repo import cancel_queued, enqueue, latest_job_states
from hyperwiki.storage.update_repo import excluded_item_ids

DEFAULT_SETTINGS = ExtractionSettings()


class ExtractionPlan(Contract):
    """What planning did with each attachment of one library.

    ``already_failed`` counts extractions that failed for the same bytes, extractor, and
    settings; they are not queued again unless retrying failed work or forcing. ``excluded``
    counts attachments whose item is only in excluded collections. ``cancelled`` counts queued
    jobs no longer wanted: their version was superseded, trashed, deleted, or excluded.
    """

    queued: NonNegativeInt
    already_queued: NonNegativeInt
    already_done: NonNegativeInt
    already_failed: NonNegativeInt
    unsupported: NonNegativeInt
    no_file: NonNegativeInt
    trashed: NonNegativeInt
    excluded: NonNegativeInt
    cancelled: NonNegativeInt


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
    only: Collection[str] | None = None,
    priority: int = 0,
) -> dict[str, ExtractionPlan]:
    """Queue extraction; returns what was done, keyed by library ID.

    Without ``only``, every library is planned and queued jobs that are no longer wanted are
    cancelled. With ``only`` (attachment IDs), just those attachments are planned, at
    ``priority``; nothing is cancelled, and libraries without any of them are left out.
    """
    plans: dict[str, ExtractionPlan] = {}
    jobs = latest_job_states(conn, EXTRACT_TEXT)
    selected = None if only is None else set(only)
    for library_id in library_ids(conn):
        states = attachment_states(conn, library_id)
        if selected is not None:
            states = [state for state in states if state.attachment_id in selected]
            if not states:
                continue
        excluded = excluded_item_ids(conn, library_id)
        counts = dict.fromkeys(ExtractionPlan.model_fields, 0)
        wanted: set[str] = set()
        for state in states:
            if state.trashed:
                counts["trashed"] += 1
                continue
            if state.item_id in excluded:
                counts["excluded"] += 1
                continue
            if state.version_id is None or state.sha256 is None:
                counts["no_file"] += 1
                continue
            spec = extraction_spec(state.version_id, state.sha256, state.content_type, settings)
            if spec is None:
                counts["unsupported"] += 1
                continue
            fingerprint = fingerprint_id(spec)
            wanted.add(fingerprint)
            if not (force or retry_failed) and jobs.get(fingerprint, (None,))[0] == "failed":
                counts["already_failed"] += 1
                continue
            result = enqueue(
                conn, spec, subject=state.version_id, now=now(), priority=priority, force=force
            )
            counts[result.outcome] += 1
        if selected is None:
            unwanted = [
                job_id
                for job_id, fingerprint in queued_version_jobs(conn, library_id, EXTRACT_TEXT)
                if fingerprint not in wanted
            ]
            counts["cancelled"] = cancel_queued(conn, unwanted, now=now())
        plans[library_id] = ExtractionPlan(**counts)
    return plans


def extract_evidence(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    now: Callable[[], datetime] = utc_now,
    max_jobs: int | None = None,
    should_stop: Callable[[], bool] = _never,
    subjects: Collection[str] | None = None,
) -> WorkerReport:
    """Run queued extraction jobs in this process until none is runnable or a limit is hit.

    ``subjects`` (attachment version IDs) limits the run to those versions' jobs.
    """
    return run_worker(
        conn,
        {EXTRACT_TEXT: TextExtractionHandler(conn, now=now)},
        owner=owner or new_owner_id(),
        now=now,
        max_jobs=max_jobs,
        should_stop=should_stop,
        subjects=subjects,
    )


def read_evidence(
    conn: sqlite3.Connection, evidence_id: str, *, settings: ExtractionSettings = DEFAULT_SETTINGS
) -> EvidenceRecord:
    """Return one evidence span and its status (superseded is judged against ``settings``).

    Never writes.
    """
    stored = get_stored_evidence(conn, evidence_id)
    source = None if stored is None else version_source(conn, stored.attachment_version_id)
    if stored is None or source is None or stored.spec.extractor is None:
        raise HyperWikiError(ErrorCode.NOT_FOUND, "no such evidence", {"evidence_id": evidence_id})
    expected = extraction_spec(source.version_id, source.sha256, source.content_type, settings)
    status: EvidenceStatus
    if source.deleted:
        status = "deleted"
    elif source.trashed:
        status = "trashed"
    elif source.item_id is not None and excluded_item_ids(
        conn, source.library_id, [source.item_id]
    ):
        status = "excluded"
    elif not (
        stored.version_is_current
        and expected is not None
        and fingerprint_id(expected) == stored.extraction_id
    ):
        status = "superseded"
    else:
        status = "current"
    return EvidenceRecord(
        id=stored.id,
        text=stored.text,
        locator=stored.locator,
        library_id=stored.library_id,
        item_key=stored.item_key,
        attachment_key=stored.attachment_key,
        attachment_version_id=stored.attachment_version_id,
        extractor=stored.spec.extractor,
        current=status == "current",
        status=status,
    )


def evidence_coverage(
    conn: sqlite3.Connection, library_id: str, *, settings: ExtractionSettings = DEFAULT_SETTINGS
) -> EvidenceCoverage:
    """Each live attachment's evidence state under ``settings``, with page and evidence counts.

    Never writes.
    """
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
    excluded_items = excluded_item_ids(conn, library_id)
    trashed = excluded = pages = pages_without_text = attachments_without_text = evidence = 0
    for attachment in attachment_states(conn, library_id):
        if attachment.trashed:
            trashed += 1
            continue
        if attachment.item_id in excluded_items:
            excluded += 1
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
        excluded=excluded,
        pages=pages,
        pages_without_text=pages_without_text,
        attachments_without_text=attachments_without_text,
        evidence=evidence,
        unsupported_types=dict(sorted(unsupported_types.items())),
        failures=failures,
    )
