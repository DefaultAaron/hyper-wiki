"""A fake monotonic clock and fake database file signatures for watcher and engine tests."""

from pathlib import Path

from hyperwiki.adapters.zotero_watch import Signature


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeFiles:
    """Stands in for stat() on zotero.sqlite and its write-ahead log."""

    def __init__(self) -> None:
        self.writes = 0

    def write(self) -> None:
        self.writes += 1

    def __call__(self, data_dir: Path) -> Signature:
        return ((4096, self.writes), (0, 0), None)
