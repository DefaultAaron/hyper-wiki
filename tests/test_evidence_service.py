import hashlib
import sqlite3
from pathlib import Path

import pytest

from hyperwiki.domain.work import Component, WorkFingerprint
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.processing.evidence_jobs import EXTRACT_TEXT, extraction_spec
from hyperwiki.processing.extractors import ExtractionSettings
from hyperwiki.processing.worker import utc_now
from hyperwiki.services.evidence import (
    ExtractionPlan,
    evidence_coverage,
    extract_evidence,
    plan_extraction,
    read_evidence,
)
from hyperwiki.services.library_import import import_snapshot
from hyperwiki.storage.job_repo import enqueue, get_job
from hyperwiki.storage.library_repo import current_attachment_version, stable_id
from hyperwiki.storage.update_repo import exclude_collection, include_collection
from pdf_factory import make_pdf
from snapshot_mutations import (
    add_file_attachment,
    delete_item,
    replace_attachment_content,
    set_trashed,
)

PDF_KEY = "ATPDFM22"
ENGLISH = ["Attention weights are normalized over every input token.", "They sum to one."]
CHINESE = ["注意力权重经过归一化处理，使其", "在所有输入词元上的总和为一。"]
PDF = make_pdf([ENGLISH, CHINESE], page_labels="0 << /S /D /St 11 >>")
OWNER = "test-worker"


def _with_pdf(snapshot: Path, content: bytes = PDF) -> Path:
    add_file_attachment(snapshot, PDF_KEY, "IT2MIX22", "mixed.pdf", content)
    return snapshot


def _only(plans: dict[str, ExtractionPlan]) -> ExtractionPlan:
    assert len(plans) == 1
    return next(iter(plans.values()))


def _evidence_ids(conn: sqlite3.Connection, key: str | None = None) -> list[str]:
    rows = conn.execute(
        """
        SELECT e.id FROM evidence AS e JOIN attachments AS a ON a.id = e.attachment_id
        WHERE ? IS NULL OR a.key = ?
        ORDER BY e.extraction_id, e.ordinal
        """,
        (key, key),
    )
    return [row[0] for row in rows]


def _page_text(conn: sqlite3.Connection, extraction_id: str, page_index: int) -> str:
    text: str = conn.execute(
        "SELECT text FROM extraction_pages WHERE extraction_id = ? AND page_index = ?",
        (extraction_id, page_index),
    ).fetchone()[0]
    return text


def _sync_and_extract(conn: sqlite3.Connection, snapshot: Path) -> str:
    library_id = import_snapshot(conn, snapshot).library_id
    plan_extraction(conn)
    extract_evidence(conn, owner=OWNER)
    return library_id


def test_english_and_chinese_spans_resolve_to_version_and_page(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, _with_pdf(bilingual_snapshot)).library_id
    plan = _only(plan_extraction(database))
    assert plan == ExtractionPlan(
        queued=3,
        already_queued=0,
        already_done=0,
        already_failed=0,
        unsupported=0,
        no_file=2,
        trashed=0,
        excluded=0,
        cancelled=0,
    )
    report = extract_evidence(database, owner=OWNER)
    assert (report.done, report.failed, report.retried) == (3, 0, 0)

    version = current_attachment_version(database, stable_id(library_id, "attachment", PDF_KEY))
    assert version is not None
    records = [
        read_evidence(database, evidence_id) for evidence_id in _evidence_ids(database, PDF_KEY)
    ]
    assert [record.locator.page_index for record in records] == [0, 1]
    assert [record.locator.page_label for record in records] == ["11", "12"]
    assert records[0].text == "\n".join(ENGLISH)
    assert records[1].text == "".join(CHINESE)
    for record in records:
        assert record.current
        assert (record.library_id, record.item_key, record.attachment_key) == (
            library_id,
            "IT2MIX22",
            PDF_KEY,
        )
        assert record.attachment_version_id == version.id
        assert record.locator.attachment_sha256 == hashlib.sha256(PDF).hexdigest()
        assert record.locator.precision == "span" and record.locator.bbox is not None
        assert record.extractor.name == "pypdfium2"
        assert record.locator.char_start is not None and record.locator.char_end is not None
        page = _page_text(database, record.locator.extractor, record.locator.page_index or 0)
        assert page[record.locator.char_start : record.locator.char_end] == record.text

    chinese = [read_evidence(database, i) for i in _evidence_ids(database, "AT2ZHN22")]
    assert [record.locator.page_index for record in chinese] == [0, 1]
    assert all(record.locator.bbox is None and record.current for record in chinese)
    assert chinese[1].text.startswith("在该模型中")


def test_unknown_evidence_is_not_found(database: sqlite3.Connection) -> None:
    with pytest.raises(HyperWikiError) as raised:
        read_evidence(database, "ev_" + "0" * 32)
    assert raised.value.code == ErrorCode.NOT_FOUND


def test_replanning_queues_nothing_new(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    _sync_and_extract(database, _with_pdf(bilingual_snapshot))
    plan = _only(plan_extraction(database))
    assert (plan.queued, plan.already_done) == (0, 3)
    assert extract_evidence(database, owner=OWNER).done == 0


def test_forced_rerun_keeps_evidence_ids(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    _sync_and_extract(database, _with_pdf(bilingual_snapshot))
    before = _evidence_ids(database)
    assert _only(plan_extraction(database, force=True)).queued == 3
    assert extract_evidence(database, owner=OWNER).done == 3
    assert _evidence_ids(database) == before
    assert database.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 3


def test_replaced_pdf_is_extracted_and_old_evidence_not_current(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    _sync_and_extract(database, snapshot)
    old = _evidence_ids(database, PDF_KEY)
    replace_attachment_content(
        snapshot, PDF_KEY, make_pdf([["A revised first page with new text."]])
    )
    import_snapshot(database, snapshot)
    assert _only(plan_extraction(database)).queued == 1
    assert extract_evidence(database, owner=OWNER).done == 1

    new = [i for i in _evidence_ids(database, PDF_KEY) if i not in old]
    assert len(new) == 1
    assert read_evidence(database, new[0]).current
    assert read_evidence(database, new[0]).text == "A revised first page with new text."
    assert all(not read_evidence(database, i).current for i in old)
    assert {read_evidence(database, i).status for i in old} == {"superseded"}


def test_changed_file_fails_without_evidence(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    import_snapshot(database, snapshot)
    plans = plan_extraction(database)
    (snapshot / "storage" / PDF_KEY / "mixed.pdf").write_bytes(make_pdf([["Edited after import."]]))
    report = extract_evidence(database, owner=OWNER)
    assert (report.done, report.failed, report.retried) == (2, 1, 0)
    assert _evidence_ids(database, PDF_KEY) == []
    error = database.execute("SELECT last_error_json FROM jobs WHERE state = 'failed'").fetchone()[
        0
    ]
    assert '"attachment_changed"' in error
    assert _only(plans).queued == 3


def test_trashed_attachment_is_not_queued(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    add_file_attachment(bilingual_snapshot, PDF_KEY, "IT2NAT22", "t.pdf", PDF, trashed=True)
    import_snapshot(database, bilingual_snapshot)
    plan = _only(plan_extraction(database))
    assert (plan.queued, plan.trashed) == (2, 1)
    extract_evidence(database, owner=OWNER)
    assert _evidence_ids(database, PDF_KEY) == []

    set_trashed(bilingual_snapshot, "attachment", PDF_KEY, False)
    import_snapshot(database, bilingual_snapshot)
    assert _only(plan_extraction(database)).queued == 1


def test_settings_change_requeues(database: sqlite3.Connection, bilingual_snapshot: Path) -> None:
    _sync_and_extract(database, _with_pdf(bilingual_snapshot))
    first = _evidence_ids(database, PDF_KEY)[0]
    smaller = ExtractionSettings(chunk_chars=400)
    assert _only(plan_extraction(database, settings=smaller)).queued == 3
    assert read_evidence(database, first).current
    assert not read_evidence(database, first, settings=smaller).current
    assert read_evidence(database, first, settings=smaller).status == "superseded"


def test_job_from_another_extractor_version_is_not_run(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, _with_pdf(bilingual_snapshot)).library_id
    version = current_attachment_version(database, stable_id(library_id, "attachment", PDF_KEY))
    assert version is not None
    spec = extraction_spec(version.id, version.sha256, "application/pdf", ExtractionSettings())
    assert spec is not None and spec.extractor is not None
    old = WorkFingerprint.model_validate(
        spec.model_dump() | {"extractor": Component(name="pypdfium2", version="0.0").model_dump()}
    )
    job = enqueue(database, old, subject=version.id, now=utc_now())
    report = extract_evidence(database, owner=OWNER)
    assert (report.done, report.failed) == (0, 1)
    failed = get_job(database, job.job_id)
    assert failed.last_error is not None and failed.last_error.code == ErrorCode.JOB_FAILED
    assert _evidence_ids(database) == []
    assert old.operation == EXTRACT_TEXT


def test_coverage_counts_every_state(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    add_file_attachment(
        snapshot, "ATHTML22", "IT2ENG22", "page.html", b"<p>x</p>", content_type="text/html"
    )
    add_file_attachment(snapshot, "ATTRSH22", "IT2TRS22", "t.pdf", PDF, trashed=True)
    library_id = _sync_and_extract(database, snapshot)
    add_file_attachment(snapshot, "ATQUEU22", "IT2NAT22", "q.pdf", make_pdf([ENGLISH]))
    import_snapshot(database, snapshot)
    plan_extraction(database)
    add_file_attachment(snapshot, "ATPEND22", "IT2NAT22", "p.pdf", make_pdf([CHINESE]))
    import_snapshot(database, snapshot)

    coverage = evidence_coverage(database, library_id)
    assert coverage.states == {
        "extracted": 3,
        "queued": 1,
        "failed": 0,
        "pending": 1,
        "unsupported": 1,
        "no_file": 2,
    }
    assert (coverage.attachments, coverage.trashed) == (8, 1)
    assert coverage.unsupported_types == {"text/html": 1}
    assert (coverage.pages, coverage.pages_without_text, coverage.evidence) == (5, 0, 5)
    assert coverage.attachments_without_text == 0
    assert coverage.failures == []


def test_corrupt_pdf_is_reported_in_coverage(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    add_file_attachment(snapshot, "ATBADF22", "IT2NAT22", "bad.pdf", b"%PDF-1.7\nbroken")
    library_id = import_snapshot(database, snapshot).library_id
    plan_extraction(database)
    report = extract_evidence(database, owner=OWNER)
    assert (report.done, report.failed, report.retried) == (3, 1, 0)

    coverage = evidence_coverage(database, library_id)
    assert (coverage.states["extracted"], coverage.states["failed"]) == (3, 1)
    [failure] = coverage.failures
    assert (failure.attachment_key, failure.code) == ("ATBADF22", ErrorCode.EXTRACTION_FAILED)
    assert "PDFium" in failure.message
    again = _only(plan_extraction(database))
    assert (again.queued, again.already_failed, again.already_done) == (0, 1, 3)
    retry = _only(plan_extraction(database, retry_failed=True))
    assert (retry.queued, retry.already_done) == (1, 3)


def test_textless_pages_are_counted_not_hidden(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot, make_pdf([[], ["12"], ENGLISH]))
    add_file_attachment(snapshot, "ATSCAN22", "IT2NAT22", "scan.pdf", make_pdf([[], []]))
    library_id = _sync_and_extract(database, snapshot)
    coverage = evidence_coverage(database, library_id)
    assert coverage.states["extracted"] == 4
    assert (coverage.pages, coverage.pages_without_text) == (8, 4)
    assert coverage.attachments_without_text == 1


def test_trashed_parent_counts_attachment_trashed(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    import_snapshot(database, snapshot)
    set_trashed(snapshot, "item", "IT2MIX22", True)  # parent of the PDF and of AT2MIS22
    import_snapshot(database, snapshot)
    plan = _only(plan_extraction(database))
    assert (plan.queued, plan.trashed, plan.no_file) == (2, 2, 1)


def test_excluded_attachments_are_not_planned_and_jobs_cancelled(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    import_snapshot(database, _with_pdf(bilingual_snapshot))
    assert _only(plan_extraction(database)).queued == 3
    exclude_collection(database, "CL2ATTN2")  # the English and Chinese papers
    plan = _only(plan_extraction(database))
    assert (plan.queued, plan.already_queued, plan.excluded, plan.cancelled) == (0, 1, 2, 2)
    assert extract_evidence(database, owner=OWNER).done == 1
    assert _evidence_ids(database, "AT2ENG22") == []
    include_collection(database, "CL2ATTN2")
    assert _only(plan_extraction(database)).queued == 2


def test_superseded_queued_job_is_cancelled_on_replan(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    import_snapshot(database, snapshot)
    plan_extraction(database)
    replace_attachment_content(snapshot, PDF_KEY, make_pdf([["Replaced before extraction."]]))
    import_snapshot(database, snapshot)
    plan = _only(plan_extraction(database))
    assert (plan.queued, plan.already_queued, plan.cancelled) == (1, 2, 1)
    report = extract_evidence(database, owner=OWNER)
    assert (report.done, report.failed) == (3, 0)


def test_selection_is_planned_ahead_and_run_alone(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    library_id = import_snapshot(database, _with_pdf(bilingual_snapshot)).library_id
    plan_extraction(database)
    attachment_id = stable_id(library_id, "attachment", PDF_KEY)
    selected = _only(plan_extraction(database, only=[attachment_id], priority=10))
    assert (selected.queued, selected.already_queued, selected.cancelled) == (0, 1, 0)
    version = current_attachment_version(database, attachment_id)
    assert version is not None
    assert extract_evidence(database, owner=OWNER, subjects={version.id}).done == 1
    assert evidence_coverage(database, library_id).states["queued"] == 2
    forced = _only(plan_extraction(database, only=[attachment_id], force=True))
    assert forced.queued == 1


def test_evidence_status_follows_lifecycle(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    _sync_and_extract(database, snapshot)
    evidence_id = _evidence_ids(database, PDF_KEY)[0]

    def status() -> str:
        changes = database.total_changes
        record = read_evidence(database, evidence_id)
        assert database.total_changes == changes  # reading never writes
        assert record.current == (record.status == "current")
        return record.status

    assert status() == "current"
    set_trashed(snapshot, "item", "IT2MIX22", True)
    import_snapshot(database, snapshot)
    assert status() == "trashed"
    exclude_collection(database, "CL2DEEP2")
    assert status() == "trashed"  # trash is reported before exclusion
    set_trashed(snapshot, "item", "IT2MIX22", False)
    import_snapshot(database, snapshot)
    assert status() == "excluded"
    include_collection(database, "CL2DEEP2")
    assert status() == "current"
    delete_item(snapshot, "IT2MIX22")
    import_snapshot(database, snapshot)
    assert status() == "deleted"


def test_coverage_counts_excluded_not_deleted(
    database: sqlite3.Connection, bilingual_snapshot: Path
) -> None:
    snapshot = _with_pdf(bilingual_snapshot)
    library_id = _sync_and_extract(database, snapshot)
    before = evidence_coverage(database, library_id)
    assert (before.attachments, before.excluded, before.states["extracted"]) == (5, 0, 3)
    english = len(_evidence_ids(database, "AT2ENG22")) + len(_evidence_ids(database, "AT2ZHN22"))

    exclude_collection(database, "CL2ATTN2")
    coverage = evidence_coverage(database, library_id)
    assert (coverage.attachments, coverage.excluded, coverage.states["extracted"]) == (3, 2, 1)
    assert coverage.evidence == before.evidence - english

    include_collection(database, "CL2ATTN2")
    delete_item(snapshot, "IT2ENG22")
    import_snapshot(database, snapshot)
    coverage = evidence_coverage(database, library_id)
    assert (coverage.attachments, coverage.excluded, coverage.states["extracted"]) == (4, 0, 2)
