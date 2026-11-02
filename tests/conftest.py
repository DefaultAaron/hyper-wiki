import shutil
from pathlib import Path

import pytest

from snapshot_mutations import BILINGUAL_DIR


@pytest.fixture
def bilingual_snapshot(tmp_path: Path) -> Path:
    """A writable copy of the bilingual fixture library."""
    target = tmp_path / "bilingual"
    shutil.copytree(BILINGUAL_DIR, target)
    return target
