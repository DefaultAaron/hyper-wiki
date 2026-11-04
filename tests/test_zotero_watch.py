from pathlib import Path

from engine_fakes import FakeClock, FakeFiles
from hyperwiki.adapters.zotero_watch import DatabaseWatcher, database_signature


def test_database_signature_reads_sizes_and_mtimes(tmp_path: Path) -> None:
    (tmp_path / "zotero.sqlite").write_bytes(b"x" * 10)
    first = database_signature(tmp_path)
    assert first[0] is not None and first[0][0] == 10
    assert first[1] is None and first[2] is None  # no -wal, no -journal
    (tmp_path / "zotero.sqlite-wal").write_bytes(b"w")
    assert database_signature(tmp_path) != first


def test_change_is_ready_only_after_quiet_period() -> None:
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=3, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    files.write()
    assert watcher.ready() is None  # just changed
    clock.sleep(2)
    files.write()  # Zotero is still writing: the quiet period starts again
    assert watcher.ready() is None
    clock.sleep(2.9)
    assert watcher.ready() is None
    clock.sleep(0.1)
    assert watcher.ready() == files(Path("Z"))


def test_no_change_is_never_ready() -> None:
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=3, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    for _ in range(10):
        clock.sleep(5)
        assert watcher.ready() is None


def test_change_back_to_synced_state_is_not_ready() -> None:
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=1, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    files.write()
    watcher.ready()
    files.writes -= 1
    clock.sleep(5)
    assert watcher.ready() is None


def test_continuous_writes_ready_after_max_wait() -> None:
    """Zotero writing every second (say, while a note is typed) still syncs within max_wait."""
    clock, files = FakeClock(), FakeFiles()
    watcher = DatabaseWatcher(Path("Z"), quiet_period=3, max_wait=10, clock=clock, signature=files)
    watcher.synced(files(Path("Z")))
    ready_at: list[float] = []
    for _ in range(25):
        files.write()
        signature = watcher.ready()
        if signature is not None:
            ready_at.append(clock.now)
            watcher.synced(signature)
        clock.sleep(1)
    assert ready_at == [10, 21]
