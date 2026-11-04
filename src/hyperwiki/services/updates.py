"""The update engine (HW-013): every update mode runs the same update step.

An update reads the library and applies it (``apply_read``: changes, trash, tombstones), plans
processing for the whole workspace (which also cancels work no longer wanted), and runs the
queued work. The modes differ only in what starts the update, whether attachment files are
rehashed, and which work runs:

- **automatic:** Zotero's database changed (debounced by the watcher);
- **scheduled:** a full check is due; every file is rehashed;
- **all:** "update all now", the full check run by the user;
- **selected:** "update selected", whose attachments are planned ahead of the queue and run on
  their own, optionally forced;
- **queue:** no read, only queued work, for example after the user resumes processing.

Pausing holds automatic, scheduled, and queue processing between jobs; syncing and planning
continue, so the workspace mirrors the library and the pending list stays exact. Manual
updates run regardless.
"""

import sqlite3
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.adapters.zotero_watch import DatabaseWatcher
from hyperwiki.domain.common import Contract
from hyperwiki.domain.updates import Selection, UpdateTrigger, full_check_due
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.processing.evidence_jobs import EXTRACT_TEXT
from hyperwiki.processing.worker import WorkerReport
from hyperwiki.services.evidence import (
    ExtractionPlan,
    extract_evidence,
    library_ids,
    plan_extraction,
)
from hyperwiki.services.library_import import SyncReport, utc_now
from hyperwiki.services.zotero_sync import LibraryReader, sync_zotero
from hyperwiki.storage.evidence_repo import attachment_states
from hyperwiki.storage.job_repo import has_runnable
from hyperwiki.storage.update_repo import (
    get_update_settings,
    last_full_check,
    record_full_check,
    selected_attachment_ids,
)

SELECTED_PRIORITY = 10
_OPERATIONS = frozenset({EXTRACT_TEXT})


class UpdateReport(Contract):
    """One update: what was read and applied, what was planned, and what ran.

    ``selected`` is the plan of the chosen attachments for "update selected". ``worker`` is
    None when processing was paused.
    """

    trigger: UpdateTrigger
    sync: SyncReport | None
    plans: dict[str, ExtractionPlan]
    selected: dict[str, ExtractionPlan] | None
    worker: WorkerReport | None
    paused: bool


def _never() -> bool:
    return False


def resolve_selection(conn: sqlite3.Connection, selection: Selection) -> list[str]:
    """Attachment IDs of the selection; ``not_found`` names any key the workspace lacks."""
    attachments, missing_items, missing_collections = selected_attachment_ids(
        conn, selection.item_keys, selection.collection_keys
    )
    if missing_items or missing_collections:
        raise HyperWikiError(
            ErrorCode.NOT_FOUND,
            "no such item or collection in the workspace",
            {"item_keys": missing_items, "collection_keys": missing_collections},
        )
    return sorted(attachments)


def _current_versions(conn: sqlite3.Connection, attachment_ids: Sequence[str]) -> set[str]:
    chosen = set(attachment_ids)
    return {
        state.version_id
        for library_id in library_ids(conn)
        for state in attachment_states(conn, library_id)
        if state.attachment_id in chosen and state.version_id is not None
    }


def run_update(
    conn: sqlite3.Connection,
    read: Callable[[bool], SyncReport] | None,
    *,
    trigger: UpdateTrigger,
    selection: Selection | None = None,
    force: bool = False,
    now: Callable[[], datetime] = utc_now,
    should_stop: Callable[[], bool] = _never,
) -> UpdateReport:
    """Run one update. ``read(verify_files)`` reads and applies the library; it is not called
    for the ``queue`` trigger.

    ``force`` (with ``selected`` only) re-extracts the selection and retries its failures.
    """
    if force and trigger != "selected":
        raise ValueError("force applies to update selected only")
    if (trigger == "selected") != (selection is not None):
        raise ValueError("a selection is required for, and only for, update selected")
    settings = get_update_settings(conn)
    sync: SyncReport | None = None
    plans: dict[str, ExtractionPlan] = {}
    selected: dict[str, ExtractionPlan] | None = None
    subjects: set[str] | None = None
    if trigger != "queue":
        if read is None:
            raise ValueError("a read is required to sync")
        full = trigger in ("scheduled", "all")
        started_at = now()
        sync = read(full)
        if full:
            record_full_check(conn, started_at)
        plans = plan_extraction(conn, now=now)
    if selection is not None:
        attachment_ids = resolve_selection(conn, selection)
        selected = plan_extraction(
            conn, now=now, only=attachment_ids, priority=SELECTED_PRIORITY, force=force
        )
        subjects = _current_versions(conn, attachment_ids)

    manual = trigger in ("all", "selected")
    worker: WorkerReport | None = None
    if manual or not settings.paused:

        def stop() -> bool:
            return should_stop() or (not manual and get_update_settings(conn).paused)

        worker = extract_evidence(conn, now=now, should_stop=stop, subjects=subjects)
    return UpdateReport(
        trigger=trigger,
        sync=sync,
        plans=plans,
        selected=selected,
        worker=worker,
        paused=settings.paused,
    )


def update_zotero(
    conn: sqlite3.Connection,
    location: ZoteroLocation,
    *,
    trigger: UpdateTrigger = "all",
    selection: Selection | None = None,
    force: bool = False,
    linked_file_roots: Sequence[Path] = (),
    now: Callable[[], datetime] = utc_now,
    reader: LibraryReader = read_zotero_library,
    should_stop: Callable[[], bool] = _never,
) -> UpdateReport:
    """Run one update of a Zotero library (see ``run_update``)."""

    def read(verify_files: bool) -> SyncReport:
        return sync_zotero(
            conn,
            location,
            linked_file_roots=linked_file_roots,
            verify_files=verify_files,
            now=now,
            reader=reader,
        )

    return run_update(
        conn,
        read,
        trigger=trigger,
        selection=selection,
        force=force,
        now=now,
        should_stop=should_stop,
    )


def run_engine(
    conn: sqlite3.Connection,
    update: Callable[[UpdateTrigger], UpdateReport],
    watcher: DatabaseWatcher,
    *,
    on_report: Callable[[UpdateReport], None],
    on_busy: Callable[[HyperWikiError], None],
    interval: float,
    rescan_interval: float,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> int:
    """Run updates until ``should_stop``; returns the number of syncs.

    Each tick reads the settings, so changes from another process apply at the next tick:
    - a full check runs when the schedule says one is due;
    - else, with automatic mode on, a sync runs at the first tick, when the watcher reports a
      settled change, and every ``rescan_interval`` (files replaced outside Zotero do not
      touch its database);
    - else, unless paused, queued work that is runnable runs.

    A busy database copy is retried at the next tick; other errors stop the loop.
    """
    syncs = 0
    last_sync: float | None = None
    while not should_stop():
        settings = get_update_settings(conn)
        trigger: UpdateTrigger | None = None
        if full_check_due(settings, last_full_check(conn), now()):
            trigger = "scheduled"
        elif settings.automatic and (
            last_sync is None
            or watcher.ready() is not None
            or clock() - last_sync >= rescan_interval
        ):
            trigger = "automatic"
        if trigger is not None:
            signature = watcher.current()  # taken first: writes during the read sync again
            try:
                report = update(trigger)
            except HyperWikiError as error:
                if error.code != ErrorCode.ZOTERO_DATABASE_BUSY:
                    raise
                on_busy(error)
            else:
                watcher.synced(signature)
                last_sync = clock()
                syncs += 1
                on_report(report)
        elif not settings.paused and has_runnable(conn, now=now(), operations=_OPERATIONS):
            on_report(update("queue"))
        sleep(interval)
    return syncs


def watch_updates(
    conn: sqlite3.Connection,
    location: ZoteroLocation,
    *,
    on_report: Callable[[UpdateReport], None],
    should_stop: Callable[[], bool],
    on_busy: Callable[[HyperWikiError], None] = lambda error: None,
    interval: float = 2.0,
    quiet_period: float = 3.0,
    max_wait: float = 60.0,
    rescan_interval: float = 600.0,
    linked_file_roots: Sequence[Path] = (),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
    reader: LibraryReader = read_zotero_library,
) -> int:
    """Keep the workspace and its evidence in step with a Zotero library (see ``run_engine``)."""
    watcher = DatabaseWatcher(
        location.data_dir, quiet_period=quiet_period, max_wait=max_wait, clock=clock
    )

    def update(trigger: UpdateTrigger) -> UpdateReport:
        return update_zotero(
            conn,
            location,
            trigger=trigger,
            linked_file_roots=linked_file_roots,
            now=now,
            reader=reader,
            should_stop=should_stop,
        )

    return run_engine(
        conn,
        update,
        watcher,
        on_report=on_report,
        on_busy=on_busy,
        interval=interval,
        rescan_interval=rescan_interval,
        should_stop=should_stop,
        sleep=sleep,
        clock=clock,
        now=now,
    )
