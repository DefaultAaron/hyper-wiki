"""Keep the workspace in step with a live Zotero library (ADR 0004).

A sync reads a byte copy of Zotero's database and applies it like any other library read
(``apply_read``): differences from the workspace are reported as change events, and records
erased from Zotero are tombstoned.
"""

import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from hyperwiki.adapters.snapshot import LoadedSnapshot
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.adapters.zotero_profile import ZoteroLocation
from hyperwiki.domain.library import KnownFile, LibraryManifest
from hyperwiki.services.library_import import SyncReport, apply_read, utc_now
from hyperwiki.storage.library_repo import known_attachment_files, library_id_for


class LibraryReader(Protocol):
    def __call__(
        self,
        data_dir: Path,
        *,
        linked_file_roots: Sequence[Path],
        base_attachment_dir: Path | None,
        known_files: Callable[[LibraryManifest], Mapping[str, KnownFile]],
        now: Callable[[], datetime],
    ) -> LoadedSnapshot: ...


def sync_zotero(
    conn: sqlite3.Connection,
    location: ZoteroLocation,
    *,
    linked_file_roots: Sequence[Path] = (),
    verify_files: bool = False,
    now: Callable[[], datetime] = utc_now,
    reader: LibraryReader = read_zotero_library,
) -> SyncReport:
    """Read the live library, report what changed since the workspace's copy, and apply it.

    Attachment files whose path, size, and modification time match the workspace are not
    hashed again, unless ``verify_files`` asks for every file to be hashed (the full check).
    A read that fails (for example ``zotero_database_busy``) raises and changes nothing.
    """
    start = time.perf_counter()
    observed = now()

    def known(manifest: LibraryManifest) -> Mapping[str, KnownFile]:
        if verify_files:
            return {}
        return known_attachment_files(conn, library_id_for(manifest.adapter, manifest.library))

    loaded = reader(
        location.data_dir,
        linked_file_roots=linked_file_roots,
        base_attachment_dir=location.base_attachment_dir,
        known_files=known,
        now=lambda: observed,
    )
    return apply_read(conn, loaded, now=observed, started=start)
