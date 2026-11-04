"""JSON command-line interface: one UTF-8 JSON document per invocation on stdout.

``watch`` keeps running and writes one document per update (JSON Lines).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO, get_args

from pydantic import BaseModel

from hyperwiki import __version__
from hyperwiki.adapters.zotero_db import require_database
from hyperwiki.adapters.zotero_profile import ZoteroLocation, discover_zotero
from hyperwiki.domain.updates import Schedule, Selection
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.services.evidence import (
    evidence_coverage,
    extract_evidence,
    library_ids,
    plan_extraction,
)
from hyperwiki.services.library_import import SyncReport, import_snapshot
from hyperwiki.services.status import StatusReport, library_status, update_status
from hyperwiki.services.updates import (
    UpdateReport,
    set_exclusions,
    update_zotero,
    watch_updates,
)
from hyperwiki.storage.database import open_database
from hyperwiki.storage.update_repo import set_update_settings

DATABASE_NAME = "hyperwiki.sqlite3"
WORKSPACE_ENV = "HYPERWIKI_WORKSPACE"
SCHEDULES: tuple[Schedule, ...] = get_args(Schedule)
_ZOTERO_KEY = re.compile(r"^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$")


def _zotero_key(value: str) -> str:
    if not _ZOTERO_KEY.match(value):
        raise argparse.ArgumentTypeError(f"not a Zotero key: {value!r}")
    return value


def _zotero_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "data_dir",
        type=Path,
        nargs="?",
        help="Zotero data directory (default: from Zotero's settings, else ~/Zotero)",
    )
    command.add_argument(
        "--linked-root",
        dest="linked_roots",
        type=Path,
        action="append",
        default=[],
        help="directory that linked files may be read from (repeatable)",
    )
    command.add_argument(
        "--base-dir",
        type=Path,
        help="Zotero's Linked Attachment Base Directory (default: from Zotero's settings)",
    )
    command.add_argument(
        "--changes", action="store_true", help="include each change event in the output"
    )


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
    sync_cmd = commands.add_parser(
        "sync",
        help="update now from a Zotero data directory: read a copy of its database, apply "
        "the changes, and extract evidence (update all, or the selected items)",
    )
    _zotero_arguments(sync_cmd)
    sync_cmd.add_argument(
        "--item",
        dest="item_keys",
        type=_zotero_key,
        action="append",
        default=[],
        help="update this item (or standalone attachment) ahead of the queue (repeatable)",
    )
    sync_cmd.add_argument(
        "--collection",
        dest="collection_keys",
        type=_zotero_key,
        action="append",
        default=[],
        help="update the items of this collection and its subcollections (repeatable)",
    )
    sync_cmd.add_argument(
        "--force",
        action="store_true",
        help="with --item or --collection: extract again even where it is already done",
    )
    watch_cmd = commands.add_parser(
        "watch",
        help="keep the workspace updated as Zotero changes and on schedule (JSON Lines)",
    )
    _zotero_arguments(watch_cmd)
    watch_cmd.add_argument("--interval", type=float, default=2.0, help="seconds between checks")
    watch_cmd.add_argument(
        "--quiet-period",
        type=float,
        default=3.0,
        help="seconds Zotero's database must stay unchanged before a sync",
    )
    watch_cmd.add_argument(
        "--max-wait",
        type=float,
        default=60.0,
        help="seconds after a change by which a sync runs even if Zotero keeps writing",
    )
    watch_cmd.add_argument(
        "--rescan-interval",
        type=float,
        default=600.0,
        help="seconds between syncs that catch files replaced outside Zotero",
    )
    watch_cmd.add_argument("--max-syncs", type=int, help=argparse.SUPPRESS)
    settings_cmd = commands.add_parser(
        "settings", help="show or change update settings and excluded collections"
    )
    settings_cmd.add_argument(
        "--automatic", choices=("on", "off"), help="sync when Zotero changes (watch)"
    )
    settings_cmd.add_argument(
        "--schedule", choices=SCHEDULES, help="how often watch runs a full check"
    )
    settings_cmd.add_argument(
        "--exclude-collection",
        dest="exclude",
        type=_zotero_key,
        action="append",
        default=[],
        help="skip items only in this collection or its subcollections (repeatable)",
    )
    settings_cmd.add_argument(
        "--include-collection",
        dest="include",
        type=_zotero_key,
        action="append",
        default=[],
        help="stop excluding this collection (repeatable)",
    )
    commands.add_parser("pause", help="hold automatic processing; changes wait in a pending list")
    commands.add_parser("resume", help="resume automatic processing")
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
    status_cmd = commands.add_parser(
        "status", help="show libraries, counts, evidence coverage, and update state"
    )
    status_cmd.add_argument(
        "--pending", action="store_true", help="also list the pending work by item"
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


def _update_result(report: UpdateReport, *, changes: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "trigger": report.trigger,
        "sync": None if report.sync is None else _sync_result(report.sync, changes=changes),
    }
    result.update(report.model_dump(mode="json", exclude={"trigger", "sync"}))
    return result


def _sync(args: argparse.Namespace, environ: Mapping[str, str]) -> dict[str, Any]:
    location = _zotero_location(args, environ)
    require_database(location.data_dir)  # before the workspace is created
    selected = bool(args.item_keys or args.collection_keys)
    conn = open_database(_workspace(args, environ) / DATABASE_NAME)
    try:
        report = update_zotero(
            conn,
            location,
            trigger="selected" if selected else "all",
            selection=(
                Selection(
                    item_keys=frozenset(args.item_keys),
                    collection_keys=frozenset(args.collection_keys),
                )
                if selected
                else None
            ),
            force=args.force,
            linked_file_roots=args.linked_roots,
        )
        return _update_result(report, changes=args.changes)
    finally:
        conn.close()


def _watch(args: argparse.Namespace, environ: Mapping[str, str], out: BinaryIO) -> None:
    location = _zotero_location(args, environ)
    require_database(location.data_dir)
    conn = open_database(_workspace(args, environ) / DATABASE_NAME)
    syncs = 0

    def on_report(report: UpdateReport) -> None:
        nonlocal syncs
        syncs += report.sync is not None
        _write(out, {"ok": True, "result": _update_result(report, changes=args.changes)})

    def on_busy(error: HyperWikiError) -> None:
        _write(out, {"ok": False, "error": error.to_info().model_dump(mode="json")})

    try:
        watch_updates(
            conn,
            location,
            on_report=on_report,
            on_busy=on_busy,
            should_stop=lambda: args.max_syncs is not None and syncs >= args.max_syncs,
            interval=args.interval,
            quiet_period=args.quiet_period,
            max_wait=args.max_wait,
            rescan_interval=args.rescan_interval,
            linked_file_roots=args.linked_roots,
        )
    finally:
        conn.close()


def _settings(conn: sqlite3.Connection, args: argparse.Namespace) -> BaseModel:
    if args.command == "pause":
        set_update_settings(conn, paused=True)
    elif args.command == "resume":
        set_update_settings(conn, paused=False)
    else:
        set_update_settings(
            conn,
            automatic=None if args.automatic is None else args.automatic == "on",
            schedule=args.schedule,
        )
        if args.include or args.exclude:
            set_exclusions(conn, exclude=args.exclude, include=args.include)
    return update_status(conn)


def _run(
    args: argparse.Namespace, environ: Mapping[str, str], out: BinaryIO
) -> BaseModel | dict[str, Any] | None:
    """Run a command; None means the command wrote its own output."""
    if args.command == "version":
        return {"version": __version__}
    if args.command == "sync":
        return _sync(args, environ)
    if args.command == "watch":
        _watch(args, environ, out)
        return None
    database_path = _workspace(args, environ) / DATABASE_NAME
    if not database_path.exists():
        if args.command == "status":
            return _status(StatusReport(libraries=[]), args)
        if args.command == "extract":
            raise HyperWikiError(
                ErrorCode.NOT_FOUND,
                "the workspace has no libraries; run sync or import first",
                {"workspace": str(database_path.parent)},
            )
    conn = open_database(database_path)
    try:
        if args.command == "import":
            report = import_snapshot(conn, args.snapshot_dir, linked_file_roots=args.linked_roots)
            return _sync_result(report, changes=args.changes)
        if args.command == "extract":
            return _extract(conn, args)
        if args.command in ("settings", "pause", "resume"):
            return _settings(conn, args)
        return _status(library_status(conn, pending=args.pending), args)
    finally:
        conn.close()


def _status(report: StatusReport, args: argparse.Namespace) -> dict[str, Any]:
    """The status document; the pending list only when asked for."""
    if not args.pending:
        return report.model_dump(mode="json", exclude={"pending"})
    return report.model_copy(update={"pending": report.pending or []}).model_dump(mode="json")


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
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "sync" and args.force and not (args.item_keys or args.collection_keys):
        parser.error("--force needs --item or --collection")
    try:
        result = _run(args, env, out)
    except KeyboardInterrupt:
        if args.command == "watch":
            return 0  # the user stopped a watch; every update was already written
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
