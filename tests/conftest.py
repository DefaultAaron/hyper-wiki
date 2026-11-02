import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from hyperwiki.storage.database import open_database
from snapshot_mutations import BILINGUAL_DIR


@pytest.fixture
def bilingual_snapshot(tmp_path: Path) -> Path:
    """A writable copy of the bilingual fixture library."""
    target = tmp_path / "bilingual"
    shutil.copytree(BILINGUAL_DIR, target)
    return target


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "workspace" / "hyperwiki.sqlite3")
    try:
        yield conn
    finally:
        conn.close()
