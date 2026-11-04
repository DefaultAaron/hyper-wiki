"""JSON command-line interface: one UTF-8 JSON document per invocation on stdout.

``import-zotero --watch`` keeps running and writes one document per sync (JSON Lines).
"""

import argparse
import json
import os
import sqlite3
import sys
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO

from pydantic import BaseModel

from hyperwiki import __version__
from hyperwiki.adapters.zotero_db import require_database
from hyperwiki.adapters.zotero_profile import ZoteroLocation, discover_zotero
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.services.evidence import (
    evidence_coverage,
    extract_evidence,
    library_ids,
    plan_extraction,
)
from hyperwiki.services.library_import import (
    StatusReport,
    SyncReport,
    import_snapshot,
    library_status,
)
from hyperwiki.services.zotero_sync import sync_zotero, watch_zotero
from hyperwiki.storage.database import open_database

DATABASE_NAME = "hyperwiki.sqlite3"
WORKSPACE_ENV = "HYPERWIKI_WORKSPACE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hyperwiki", description="Evidence-backed research wiki for literature libraries."
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help=f"workspace directory (default: ${WORKSPACE_ENV} or ~/.hyperwiki)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    import_cmd = commands.add_parser("import", help="import a library snapshot directory")
    import_cmd.add_argument("snapshot_dir", type=Path)
    import_cmd.add_argument(
        "--linked-root",
        dest="linked_roots",
        type=Path,
        action="append",
        default=[],
        help="directory that linked files may be read from (repeatable)",
    )
    import_cmd.add_argument(
        "--changes", action="store_true", help="include each change event in the output"
    )
    zotero_cmd = commands.add_parser(
        "import-zotero",
        help="sync the user library from a Zotero data directory (reads a copy of its database)",
    )
    zotero_cmd.add_argument(
        "data_dir",
        type=Path,
        nargs="?",
        help="Zotero data directory (default: from Zotero's settings, else ~/Zotero)",
    )
    zotero_cmd.add_argument(
        "--linked-root",
        dest="linked_roots",
        type=Path,
        action="append",
        default=[],
        help="directory that linked files may be read from (repeatable)",
    )
    zotero_cmd.add_argument(
        "--base-dir",
        type=Path,
        help="Zotero's Linked Attachment Base Directory (default: from Zotero's settings)",
    )
    zotero_cmd.add_argument(
        "--changes", action="store_true", help="include each change event in the output"
    )
    zotero_cmd.add_argument(
        "--watch", action="store_true", help="keep syncing as Zotero changes (JSON Lines)"
    )
    zotero_cmd.add_argument(
        "--interval", type=float, default=2.0, help="seconds between checks (watch)"
    )
    zotero_cmd.add_argument(
        "--quiet-period",
        type=float,
        default=3.0,
        help="seconds the database must stay unchanged before a sync (watch)",
    )
    zotero_cmd.add_argument(
        "--rescan-interval",
        type=float,
        default=600.0,
        help="seconds between full syncs that catch files replaced outside Zotero (watch)",
    )
    zotero_cmd.add_argument("--max-syncs", type=int, help=argparse.SUPPRESS)
    extract_cmd = commands.add_parser(
        "extract", help="extract evidence from attachments that need it and show coverage"
    )
    extract_cmd.add_argument(
        "--force", action="store_true", help="extract again even where it is already done"
    )
    extract_cmd.add_argument(
        "--retry-failed", action="store_true", help="queue failed extractions again"
    )
    extract_cmd.add_argument("--max-jobs", type=int, help="stop after this many extraction jobs")
    commands.add_parser(
        "status", help="show imported libraries, their counts, and evidence coverage"
    )
    commands.add_parser("version", help="show the Hyper-Wiki version")
    return parser


def _workspace(args: argparse.Namespace, environ: Mapping[str, str]) -> Path:
    if args.workspace is not None:
        workspace: Path = args.workspace
        return workspace
    from_env = environ.get(WORKSPACE_ENV)
    if from_env:
        return Path(from_env)
    return Path.home() / ".hyperwiki"


def _home(environ: Mapping[str, str]) -> Path | None:
    home = environ.get("HOME") or environ.get("USERPROFILE")
    return Path(home) if home else None


def _zotero_location(args: argparse.Namespace, environ: Mapping[str, str]) -> ZoteroLocation:
    """Explicit arguments, else Zotero's own settings (only with a known home directory)."""
    data_dir: Path | None = None if args.data_dir is None else args.data_dir.expanduser()
    base_dir: Path | None = None if args.base_dir is None else args.base_dir.expanduser()
    home = _home(environ)
    if home is None:
        if data_dir is None:
            raise HyperWikiError(
                ErrorCode.NOT_FOUND,
                "cannot locate the Zotero data directory; pass it as DATA_DIR",
            )
        return ZoteroLocation(data_dir=data_dir, base_attachment_dir=base_dir, profile_dir=None)
    return discover_zotero(
        data_dir=data_dir,
        base_attachment_dir=base_dir,
        home=home,
        environ=environ,
        platform=sys.platform,
    )


def _sync_result(report: SyncReport, *, changes: bool) -> dict[str, Any]:
    result = report.import_report.model_dump(mode="json")
    result["change_counts"] = report.change_counts
    result["duration_ms"] = report.duration_ms
    if changes:
        result["changes"] = [event.model_dump(mode="json") for event in report.changes]
    return result


def _import_zotero(
    args: argparse.Namespace, environ: Mapping[str, str], out: BinaryIO
) -> dict[str, Any] | None:
    location = _zotero_location(args, environ)
    require_database(location.data_dir)  # before the workspace is created
    conn = open_database(_workspace(args, environ) / DATABASE_NAME)
    try:
        if not args.watch:
            report = sync_zotero(conn, location, linked_file_roots=args.linked_roots)
            return _sync_result(report, changes=args.changes)
        syncs = 0

        def on_sync(report: SyncReport) -> None:
            nonlocal syncs
            syncs += 1
            _write(out, {"ok": True, "result": _sync_result(report, changes=args.changes)})

        def on_busy(error: HyperWikiError) -> None:
            _write(out, {"ok": False, "error": error.to_info().model_dump(mode="json")})

        watch_zotero(
            conn,
            location,
            on_sync=on_sync,
            on_busy=on_busy,
            should_stop=lambda: args.max_syncs is not None and syncs >= args.max_syncs,
            interval=args.interval,
            quiet_period=args.quiet_period,
            rescan_interval=args.rescan_interval,
            linked_file_roots=args.linked_roots,
        )
        return None
    finally:
        conn.close()


def _run(
    args: argparse.Namespace, environ: Mapping[str, str], out: BinaryIO
) -> BaseModel | dict[str, Any] | None:
    """Run a command; None means the command wrote its own output."""
    if args.command == "version":
        return {"version": __version__}
    if args.command == "import-zotero":
        return _import_zotero(args, environ, out)
    database_path = _workspace(args, environ) / DATABASE_NAME
    if not database_path.exists():
        if args.command == "status":
            return StatusReport(libraries=[])
        if args.command == "extract":
            raise HyperWikiError(
                ErrorCode.NOT_FOUND,
                "the workspace has no libraries; run import or import-zotero first",
                {"workspace": str(database_path.parent)},
            )
    conn = open_database(database_path)
    try:
        if args.command == "import":
            report = import_snapshot(conn, args.snapshot_dir, linked_file_roots=args.linked_roots)
            return _sync_result(report, changes=args.changes)
        if args.command == "extract":
            return _extract(conn, args)
        return library_status(conn)
    finally:
        conn.close()


def _extract(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    plans = plan_extraction(conn, force=args.force, retry_failed=args.retry_failed)
    report = extract_evidence(conn, max_jobs=args.max_jobs)
    return {
        "plans": {library_id: plan.model_dump(mode="json") for library_id, plan in plans.items()},
        "worker": report.model_dump(mode="json"),
        "coverage": {
            library_id: evidence_coverage(conn, library_id).model_dump(mode="json")
            for library_id in library_ids(conn)
        },
    }


def _write(stdout: BinaryIO, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
    stdout.flush()


def _fail(stdout: BinaryIO, info: ErrorInfo) -> int:
    _write(stdout, {"ok": False, "error": info.model_dump(mode="json")})
    return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: BinaryIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout.buffer
    env = environ if environ is not None else os.environ
    args = _parser().parse_args(argv)
    try:
        result = _run(args, env, out)
    except KeyboardInterrupt:
        if getattr(args, "watch", False):
            return 0  # the user stopped a watch; every sync was already written
        raise
    except HyperWikiError as error:
        return _fail(out, error.to_info())
    except sqlite3.Error as error:
        traceback.print_exc(file=sys.stderr)
        return _fail(
            out,
            ErrorInfo(
                code=ErrorCode.DATABASE_ERROR,
                message=f"workspace database error: {error}",
                details={"type": type(error).__name__},
            ),
        )
    except OSError as error:
        traceback.print_exc(file=sys.stderr)
        return _fail(
            out,
            ErrorInfo(
                code=ErrorCode.IO_ERROR,
                message=f"file system error: {error.strerror or error}",
                details={"path": None if error.filename is None else str(error.filename)},
            ),
        )
    if result is None:
        return 0
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    _write(out, {"ok": True, "result": result})
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
