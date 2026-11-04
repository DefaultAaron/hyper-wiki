"""Notice that Zotero changed its database, without opening it (ADR 0004).

The signature of a Zotero data directory is the size and modification time of ``zotero.sqlite``
and its ``-wal`` and ``-journal`` files. Zotero writes the write-ahead log on every commit, so
the signature changes with every change to the library. A change is ready to read once the
signature has stayed the same for a quiet period, so a burst of writes (an item, then its PDF,
then a rename) is read once. If Zotero keeps writing, for example while a note is typed, the
change is ready anyway once ``max_wait`` has passed since it was first seen.
"""

import time
from collections.abc import Callable
from pathlib import Path

from hyperwiki.adapters.zotero_db import ZOTERO_DATABASE

Signature = tuple[tuple[int, int] | None, ...]

_FILES = (ZOTERO_DATABASE, ZOTERO_DATABASE + "-wal", ZOTERO_DATABASE + "-journal")


def database_signature(data_dir: Path) -> Signature:
    """(size, mtime in ns) of each database file, or None for a file that does not exist."""
    states: list[tuple[int, int] | None] = []
    for name in _FILES:
        try:
            stat = (data_dir / name).stat()
        except FileNotFoundError:
            states.append(None)
        else:
            states.append((stat.st_size, stat.st_mtime_ns))
    return tuple(states)


class DatabaseWatcher:
    """Tells when the database differs from its last synced state and has gone quiet."""

    def __init__(
        self,
        data_dir: Path,
        *,
        quiet_period: float,
        max_wait: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        signature: Callable[[Path], Signature] = database_signature,
    ) -> None:
        self.data_dir = data_dir
        self.quiet_period = quiet_period
        self.max_wait = max_wait
        self.clock = clock
        self.signature = signature
        self._synced: Signature | None = None
        self._seen: Signature | None = None
        self._seen_at = 0.0
        self._unsynced_since: float | None = None

    def current(self) -> Signature:
        return self.signature(self.data_dir)

    def ready(self) -> Signature | None:
        """The current signature if it differs from the synced one and has been unchanged for
        the quiet period, or has differed for ``max_wait``; else None."""
        now, signature = self.clock(), self.current()
        if signature != self._seen:
            self._seen, self._seen_at = signature, now
        if signature == self._synced:
            self._unsynced_since = None
            return None
        if self._unsynced_since is None:
            self._unsynced_since = now
        if now - self._seen_at >= self.quiet_period or now - self._unsynced_since >= self.max_wait:
            return signature
        return None

    def synced(self, signature: Signature) -> None:
        """Record the signature taken just before a successful read."""
        self._synced = signature
        self._unsynced_since = None
