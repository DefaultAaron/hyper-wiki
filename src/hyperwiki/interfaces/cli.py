"""JSON command-line interface: one UTF-8 JSON document per invocation on stdout."""

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
from hyperwiki.adapters.zotero_db import read_zotero_library
from hyperwiki.errors import ErrorCode, ErrorInfo, HyperWikiError
from hyperwiki.services.library_import import (
    StatusReport,
    import_loaded,
    import_snapshot,
    library_status,
)
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
    zotero_cmd = commands.add_parser(
        "import-zotero",
        help="import the user library from a Zotero data directory (reads a copy of its database)",
    )
    zotero_cmd.add_argument("data_dir", type=Path, help="Zotero data directory, e.g. ~/Zotero")
    zotero_cmd.add_argument(
        "--linked-root",
        dest="linked_roots",
        type=Path,
        action="append",
        default=[],
        help="directory that linked files may be read from (repeatable)",
    )
    commands.add_parser("status", help="show imported libraries and their counts")
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


def _run(args: argparse.Namespace, environ: Mapping[str, str]) -> BaseModel | dict[str, Any]:
    if args.command == "version":
        return {"version": __version__}
    database_path = _workspace(args, environ) / DATABASE_NAME
    if args.command == "status" and not database_path.exists():
        return StatusReport(libraries=[])
    loaded = None
    if args.command == "import-zotero":
        loaded = read_zotero_library(args.data_dir, linked_file_roots=args.linked_roots)
    conn = open_database(database_path)
    try:
        if args.command == "import":
            return import_snapshot(conn, args.snapshot_dir, linked_file_roots=args.linked_roots)
        if loaded is not None:
            return import_loaded(conn, loaded)
        return library_status(conn)
    finally:
        conn.close()


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
        result = _run(args, env)
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
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    _write(out, {"ok": True, "result": result})
    return 0


def entrypoint() -> None:
    raise SystemExit(main())
