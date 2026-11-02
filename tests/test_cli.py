import io
import json
from pathlib import Path
from typing import Any

import hyperwiki
from hyperwiki.interfaces.cli import main


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
    assert payload["result"] == {"libraries": []}
    assert not ws.exists()


def test_version_command() -> None:
    code, payload, _ = run_cli(["version"])
    assert code == 0
    assert payload["result"] == {"version": hyperwiki.__version__}
