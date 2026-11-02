import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import hyperwiki
from hyperwiki.domain.work import WorkFingerprint
from hyperwiki.interfaces.cli import DATABASE_NAME, main
from hyperwiki.storage.database import open_database
from hyperwiki.storage.job_repo import enqueue


def run_cli(
    args: list[str], environ: dict[str, str] | None = None
) -> tuple[int, dict[str, Any], bytes]:
    out = io.BytesIO()
    code = main(args, stdout=out, environ=environ or {})
    raw = out.getvalue()
    return code, json.loads(raw.decode("utf-8")), raw


def test_import_then_status(tmp_path: Path, bilingual_snapshot: Path) -> None:
    ws = str(tmp_path / "ws")
    code, payload, _ = run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    assert code == 0 and payload["ok"] is True
    assert payload["result"]["counts"]["items"] == 6
    code, payload, _ = run_cli(["--workspace", ws, "status"])
    assert code == 0
    assert payload["result"]["libraries"][0]["last_snapshot_id"] == "bilingual-2026-11-02"


def test_status_reports_job_counts(tmp_path: Path, bilingual_snapshot: Path) -> None:
    ws = tmp_path / "ws"
    run_cli(["--workspace", str(ws), "import", str(bilingual_snapshot)])
    conn = open_database(ws / DATABASE_NAME)
    try:
        spec = WorkFingerprint(
            operation="test.work", operation_version=1, inputs={"n": "1"}, schema_version=1
        )
        enqueue(conn, spec, subject="s1", now=datetime(2026, 11, 3, tzinfo=UTC))
    finally:
        conn.close()
    code, payload, _ = run_cli(["--workspace", str(ws), "status"])
    assert code == 0
    assert payload["result"]["jobs"] == {
        "test.work": {"queued": 1, "leased": 0, "done": 0, "failed": 0, "cancelled": 0}
    }
    assert payload["result"]["libraries"][0]["counts"]["attachment_versions"] == 2


def test_output_is_utf8_json_with_chinese(tmp_path: Path, bilingual_snapshot: Path) -> None:
    ws = str(tmp_path / "ws")
    run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    _, _, raw = run_cli(["--workspace", ws, "status"])
    assert "合成双语文库".encode() in raw


def test_missing_snapshot_returns_error_envelope(tmp_path: Path) -> None:
    ws = str(tmp_path / "ws")
    code, payload, _ = run_cli(["--workspace", ws, "import", str(tmp_path / "nope")])
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"


def test_workspace_from_environment(tmp_path: Path, bilingual_snapshot: Path) -> None:
    env_ws = tmp_path / "env-ws"
    code, _, _ = run_cli(["import", str(bilingual_snapshot)], {"HYPERWIKI_WORKSPACE": str(env_ws)})
    assert code == 0
    assert (env_ws / "hyperwiki.sqlite3").exists()


def test_status_without_database_creates_nothing(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    code, payload, _ = run_cli(["--workspace", str(ws), "status"])
    assert code == 0
    assert payload["result"] == {"libraries": [], "jobs": {}}
    assert not ws.exists()


def test_version_command() -> None:
    code, payload, _ = run_cli(["version"])
    assert code == 0
    assert payload["result"] == {"version": hyperwiki.__version__}


def test_corrupt_database_returns_error_envelope(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hyperwiki.sqlite3").write_bytes(b"this is not a database" * 100)
    code, payload, _ = run_cli(["--workspace", str(ws), "status"])
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "database_error"


def test_unusable_workspace_returns_error_envelope(
    tmp_path: Path, bilingual_snapshot: Path
) -> None:
    ws = tmp_path / "ws"
    ws.write_text("a file, not a directory", encoding="utf-8")
    code, payload, _ = run_cli(["--workspace", str(ws), "import", str(bilingual_snapshot)])
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "io_error"
