"""HW-006, real library: measure the read-only detection candidates while Zotero runs.

Read-only. Makes GET requests to the running Zotero's local API and reads byte copies of
``zotero.sqlite`` with Hyper-Wiki's copy reader; never opens Zotero's database, never writes to
Zotero, never changes the library. Prints counts and timings only, no titles or paths.

    uv run --locked python experiments/hw006-change-detection/observe.py \
        --zotero-dir ~/Zotero [--idle 60]
"""

import argparse
import json
import platform
import sqlite3
import statistics
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyperwiki.adapters import zotero_db

DATABASE_FILES = ("zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-journal")


def get(
    url: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes, float]:
    request = urllib.request.Request(url, headers={"Zotero-API-Version": "3", **(headers or {})})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            status, response_headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        body, status, response_headers = error.read(), error.code, dict(error.headers)
    return status, response_headers, body, (time.perf_counter() - start) * 1000


def timings(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "n": len(values),
        "median": round(statistics.median(values), 1),
        "max": round(max(values), 1),
    }


def file_state(data_dir: Path) -> list[tuple[int, int] | None]:
    states: list[tuple[int, int] | None] = []
    for name in DATABASE_FILES:
        try:
            stat = (data_dir / name).stat()
            states.append((stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            states.append(None)
    return states


def copy_and_read(data_dir: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hw006-observe-") as temporary:
        start = time.perf_counter()
        copy = zotero_db._copy_database(data_dir, Path(temporary), 3)
        copied = time.perf_counter()
        conn = sqlite3.connect(copy)
        try:
            conn.row_factory = sqlite3.Row
            manifest, _ = zotero_db._read_copy(conn, datetime.now(UTC))
            delete_log = conn.execute("SELECT count(*) FROM syncDeleteLog").fetchone()[0]
            trashed_rows = conn.execute("SELECT count(*) FROM deletedItems").fetchone()[0]
        finally:
            conn.close()
        done = time.perf_counter()
    return {
        "copy_ms": (copied - start) * 1000,
        "read_ms": (done - copied) * 1000,
        "objects": len(manifest.items)
        + len(manifest.attachments)
        + len(manifest.notes)
        + len(manifest.annotations),
        "client_version": int(manifest.snapshot_id.removeprefix("zotero-")),
        "delete_log_entries": delete_log,
        "trashed_rows": trashed_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zotero-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=23119)
    parser.add_argument("--idle", type=float, default=60, help="seconds of idle polling")
    args = parser.parse_args()
    data_dir: Path = args.zotero_dir.expanduser()
    base = f"http://127.0.0.1:{args.port}/api/users/0"
    files_before = file_state(data_dir)

    result: dict[str, Any] = {
        "measured": datetime.now().date().isoformat(),
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "database_mb": round((data_dir / "zotero.sqlite").stat().st_size / 2**20, 1),
        "wal_mb": round(
            (data_dir / "zotero.sqlite-wal").stat().st_size / 2**20
            if (data_dir / "zotero.sqlite-wal").exists()
            else 0,
            1,
        ),
    }

    status, headers, body, ms = get(base + "/items?format=versions&includeTrashed=1")
    if status != 200:
        raise SystemExit(f"local API: HTTP {status}; is Zotero running with the local API on?")
    version = int(headers["Last-Modified-Version"])
    result["local_api"] = {
        "keys": len(json.loads(body)),
        "full_list_ms": round(ms, 1),
        "library_version": version,
    }
    _, _, trash, trash_ms = get(base + "/items/trash?format=keys")
    result["local_api"]["trash_keys"] = len(trash.split())
    result["local_api"]["trash_list_ms"] = round(trash_ms, 1)
    _, _, _, top_ms = get(base + "/items/top?format=json&limit=50")
    result["local_api"]["top_50_json_ms"] = round(top_ms, 1)

    copies = [copy_and_read(data_dir) for _ in range(5)]
    first = copies[0]
    result["copy_reader"] = {
        "objects": first["objects"],
        "client_version": first["client_version"],
        "delete_log_entries": first["delete_log_entries"],
        "trashed_rows": first["trashed_rows"],
        "copy_ms": timings([c["copy_ms"] for c in copies]),
        "read_ms": timings([c["read_ms"] for c in copies]),
    }
    start = time.perf_counter()
    loaded = zotero_db.read_zotero_library(data_dir)
    result["copy_reader"]["full_read_with_hashing_s"] = round(time.perf_counter() - start, 1)
    result["copy_reader"]["files_hashed"] = sum(
        1 for f in loaded.files.values() if f.availability == "available"
    )

    # Idle: what does each detector cost, and how often do Zotero's files change by themselves?
    not_modified, changed, poll_ms = 0, 0, []
    file_changes, last_files = 0, file_state(data_dir)
    deadline = time.monotonic() + args.idle
    while time.monotonic() < deadline:
        status, headers, _, ms = get(
            base + "/items?format=versions&includeTrashed=1",
            {"If-Modified-Since-Version": str(version)},
        )
        poll_ms.append(ms)
        if status == 304:
            not_modified += 1
        else:
            changed += 1
            version = int(headers.get("Last-Modified-Version", version))
        files = file_state(data_dir)
        if files != last_files:
            file_changes += 1
            last_files = files
        time.sleep(1)
    result["idle"] = {
        "seconds": args.idle,
        "local_api_304": not_modified,
        "local_api_changed": changed,
        "local_api_poll_ms": timings(poll_ms),
        "database_file_changes": file_changes,
    }
    result["zotero_wrote_its_files_during_run"] = file_state(data_dir) != files_before
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
