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


def test_cli_extract_reports_plan_worker_and_coverage(
    tmp_path: Path, bilingual_snapshot: Path
) -> None:
    ws = str(tmp_path / "ws")
    _, imported, _ = run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    library_id = imported["result"]["library_id"]
    code, payload, _ = run_cli(["--workspace", ws, "extract"])
    assert code == 0 and payload["ok"] is True
    result = payload["result"]
    assert result["plans"][library_id]["queued"] == 2
    assert result["plans"][library_id]["no_file"] == 2
    assert (result["worker"]["done"], result["worker"]["failed"]) == (2, 0)
    assert result["coverage"][library_id]["states"]["extracted"] == 2
    assert result["coverage"][library_id]["evidence"] == 3

    code, payload, _ = run_cli(["--workspace", ws, "extract", "--force", "--max-jobs", "1"])
    assert code == 0
    assert payload["result"]["plans"][library_id]["queued"] == 2
    assert payload["result"]["worker"]["done"] == 1


def test_cli_status_includes_evidence_coverage(tmp_path: Path, bilingual_snapshot: Path) -> None:
    ws = str(tmp_path / "ws")
    run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    _, before, _ = run_cli(["--workspace", ws, "status"])
    assert before["result"]["libraries"][0]["evidence"]["states"]["pending"] == 2
    run_cli(["--workspace", ws, "extract"])
    _, after, _ = run_cli(["--workspace", ws, "status"])
    evidence = after["result"]["libraries"][0]["evidence"]
    assert evidence["states"]["extracted"] == 2 and evidence["states"]["pending"] == 0


def test_cli_extract_without_workspace_is_not_found(tmp_path: Path) -> None:
    ws = tmp_path / "missing"
    code, payload, _ = run_cli(["--workspace", str(ws), "extract"])
    assert code == 1 and payload["error"]["code"] == "not_found"
    assert not ws.exists()


def test_cli_extract_retry_failed(tmp_path: Path, bilingual_snapshot: Path) -> None:
    (bilingual_snapshot / "storage" / "AT2ZHN22" / "中文论文.txt").write_bytes(b"\xff\xfe bad")
    ws = str(tmp_path / "ws")
    run_cli(["--workspace", ws, "import", str(bilingual_snapshot)])
    _, first, _ = run_cli(["--workspace", ws, "extract"])
    library_id = next(iter(first["result"]["plans"]))
    assert first["result"]["worker"]["failed"] == 1
    assert first["result"]["coverage"][library_id]["failures"][0]["code"] == "extraction_failed"
    _, second, _ = run_cli(["--workspace", ws, "extract"])
    assert second["result"]["plans"][library_id]["already_failed"] == 1
    _, third, _ = run_cli(["--workspace", ws, "extract", "--retry-failed"])
    assert third["result"]["plans"][library_id]["queued"] == 1
    assert third["result"]["worker"]["failed"] == 1
