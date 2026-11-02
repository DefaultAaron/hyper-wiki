"""HW-006 spike: how can Hyper-Wiki notice changes to a Zotero library while Zotero runs?

Runs a real Zotero (10.x) on a throwaway profile and a throwaway data directory, applies a
scripted series of changes through Zotero's own JavaScript API, and records when and how each
detection candidate sees them:

- ``local_api``: polling Zotero's local API (``/api/users/0/items?format=versions``) with
  ``If-Modified-Since-Version``;
- ``db_copy``: watching ``zotero.sqlite`` and its write-ahead log, and reading a byte copy with
  Hyper-Wiki's Zotero copy reader when they change;
- ``notifier``: a plugin's ``Zotero.Notifier`` observer (the test driver logs every event);
- ``storage``: watching attachment files on disk.

It also checks safety while Zotero runs: whether the live database can be opened directly,
whether copies are refused or torn while Zotero writes, and whether the reader leaves files in
Zotero's data directory.

The user's own Zotero profile and library are never used. The throwaway profile gets its own
data directory (``-datadir``), its own local server port, and the test driver plugin, which
stays inactive unless the data directory holds the ``hw006-throwaway`` marker this script
writes. Everything goes in a private run directory outside the repository; the summary written
to ``results/`` holds only timings and counts from synthetic data.

    uv run --locked python experiments/hw006-change-detection/spike.py \
        --private /path/outside/the/repo
"""

import argparse
import contextlib
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyperwiki.adapters import zotero_db
from hyperwiki.errors import ErrorCode, HyperWikiError

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PLUGIN_ID = "hw006-driver@hyper-wiki.invalid"
MARKER = "hw006-throwaway"
DATABASE_FILES = ("zotero.sqlite", "zotero.sqlite-wal", "zotero.sqlite-journal")
DETECTORS = ("notifier", "local_api", "local_api_trash", "db_copy", "storage")


def now_ms() -> float:
    return time.time() * 1000


def default_zotero() -> Path:
    if sys.platform == "darwin":
        return Path("/Applications/Zotero.app/Contents/MacOS/zotero")
    if sys.platform == "win32":
        for base in (
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            candidate = Path(base) / "Zotero" / "zotero.exe"
            if candidate.is_file():
                return candidate
        return Path(r"C:\Program Files\Zotero\zotero.exe")
    found = shutil.which("zotero")
    return Path(found) if found else Path("/opt/zotero/zotero")


# --- synthetic files -----------------------------------------------------------------------


def make_pdf(path: Path, text: str) -> None:
    """Write a one-page PDF with an ASCII text line; offsets in the xref table are exact."""
    content = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


# --- HTTP ----------------------------------------------------------------------------------


def http(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60,
) -> tuple[int, dict[str, str], bytes]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class Zotero:
    """A throwaway Zotero instance with the HW-006 test driver."""

    def __init__(self, executable: Path, run_dir: Path, port: int) -> None:
        self.executable = executable
        self.profile = run_dir / "profile"
        self.data = run_dir / "data"
        self.base = run_dir / "linked-base"
        self.log_path = run_dir / "notifier.jsonl"
        self.output = run_dir / "zotero-output.log"
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen[bytes] | None = None

    def prepare(self) -> None:
        for directory in (self.profile / "extensions", self.data, self.base):
            directory.mkdir(parents=True, exist_ok=True)
        self.log_path.touch()
        (self.data / MARKER).write_text("HW-006 throwaway data directory\n", encoding="utf-8")
        prefs = {
            "extensions.zotero.dataDir": str(self.data),
            "extensions.zotero.useDataDir": True,
            "extensions.zotero.httpServer.port": self.port,
            "extensions.zotero.httpServer.localAPI.enabled": True,
            "extensions.zotero.sync.autoSync": False,
            "extensions.zotero.firstRun2": False,
            "extensions.zotero.firstRunGuidance": False,
            "extensions.zotero.automaticScraperUpdates": False,
            "extensions.zotero.saveRelativeAttachmentPath": True,
            "extensions.zotero.baseAttachmentPath": str(self.base),
            "app.update.auto": False,
            "app.update.enabled": False,
            "extensions.autoDisableScopes": 0,
            "xpinstall.signatures.required": False,
            "extensions.hw006.logPath": str(self.log_path),
            # A fresh profile otherwise tries to install Zotero's word processor plugins
            # into Word and LibreOffice on this machine.
            "extensions.zoteroMacWordIntegration.skipInstallation": True,
            "extensions.zoteroWinWordIntegration.skipInstallation": True,
            "extensions.zoteroOpenOfficeIntegration.skipInstallation": True,
        }
        lines = [f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k, v in prefs.items()]
        (self.profile / "prefs.js").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.profile / "extensions" / PLUGIN_ID).write_text(
            str(HERE / "driver") + os.sep, encoding="utf-8"
        )

    def start(self, timeout: float = 180) -> dict[str, Any]:
        with self.output.open("ab") as output:
            self.process = subprocess.Popen(
                [
                    str(self.executable),
                    "-profile",
                    str(self.profile),
                    "-no-remote",
                    "-datadir",
                    str(self.data),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SystemExit(f"Zotero exited early with code {self.process.returncode}")
            with contextlib.suppress(OSError):
                status, _, body = http("GET", self.url + "/hw006/ping", timeout=2)
                if status == 200:
                    info: dict[str, Any] = json.loads(body)
                    if Path(info["dataDir"]).resolve() != self.data.resolve():
                        self.stop()
                        raise SystemExit("Zotero opened another data directory; stopped it")
                    return info
            time.sleep(0.5)
        self.stop()
        raise SystemExit("the test driver did not answer; see zotero-output.log")

    def act(self, action: str, **args: Any) -> dict[str, Any]:
        status, _, body = http("POST", self.url + "/hw006/act", {"action": action, "args": args})
        result: dict[str, Any] = json.loads(body)
        if status != 200 or not result.get("ok"):
            raise RuntimeError(f"{action} failed: {result}")
        return result

    def stop(self, timeout: float = 60) -> float | None:
        """Quit Zotero through the driver; return how long it took to exit, in seconds."""
        if self.process is None or self.process.poll() is not None:
            return None
        start = time.monotonic()
        with contextlib.suppress(OSError):
            http("POST", self.url + "/hw006/quit", {}, timeout=5)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            return None
        return time.monotonic() - start


# --- detectors -----------------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.observations: list[dict[str, Any]] = []

    def add(self, detector: str, t: float, kind: str, keys: list[str], **detail: Any) -> None:
        with self.lock:
            self.observations.append(
                {"detector": detector, "t": t, "kind": kind, "keys": keys, **detail}
            )

    def between(self, start: float, end: float) -> list[dict[str, Any]]:
        with self.lock:
            return [o for o in self.observations if start <= o["t"] < end]


class Poller(threading.Thread):
    name_ = "poller"
    interval = 1.0

    def __init__(self, recorder: Recorder) -> None:
        super().__init__(daemon=True)
        self.recorder = recorder
        self.stopping = threading.Event()
        self.paused = threading.Event()
        self.errors: list[str] = []
        self.polls = 0

    def run(self) -> None:
        while not self.stopping.is_set():
            if not self.paused.is_set():
                try:
                    self.poll()
                    self.polls += 1
                except Exception as error:  # recorded, not fatal: the spike measures failures
                    self.errors.append(f"{type(error).__name__}: {error}")
            self.stopping.wait(self.interval)

    def poll(self) -> None:
        raise NotImplementedError


def diff_states(
    old: dict[str, tuple[Any, ...]], new: dict[str, tuple[Any, ...]]
) -> list[tuple[str, str]]:
    """Return (kind, key) for added, removed, trashed, restored, and modified objects."""
    changes = [("added", key) for key in new.keys() - old.keys()]
    changes += [("removed", key) for key in old.keys() - new.keys()]
    for key in new.keys() & old.keys():
        (old_version, old_trashed), (new_version, new_trashed) = old[key][:2], new[key][:2]
        if new_trashed and not old_trashed:
            changes.append(("trashed", key))
        elif old_trashed and not new_trashed:
            changes.append(("restored", key))
        elif new_version != old_version:
            changes.append(("modified", key))
    return changes


class LocalApi(Poller):
    name_ = "local_api"
    interval = 0.25

    def __init__(self, recorder: Recorder, url: str) -> None:
        super().__init__(recorder)
        self.base = url + "/api/users/0"
        self.version = 0
        self.state: dict[str, tuple[Any, ...]] | None = None
        self.request_ms: list[float] = []
        self.not_modified = 0

    def get(self, path: str, since: int | None = None) -> tuple[int, dict[str, str], Any]:
        headers = {"Zotero-API-Version": "3"}
        if since:
            headers["If-Modified-Since-Version"] = str(since)
        start = now_ms()
        status, response_headers, body = http("GET", self.base + path, headers=headers)
        self.request_ms.append(now_ms() - start)
        return status, response_headers, json.loads(body) if status == 200 else None

    def snapshot(self) -> tuple[int, dict[str, tuple[Any, ...]]] | None:
        status, headers, items = self.get("/items?format=versions&includeTrashed=1", self.version)
        if status == 304:
            self.not_modified += 1
            return None
        if status != 200:
            raise RuntimeError(f"items: HTTP {status}")
        version = int(headers["Last-Modified-Version"])
        _, _, trash = self.get("/items/trash?format=versions")
        _, _, collections = self.get("/collections?format=versions")
        state = {key: (v, key in trash, "item") for key, v in items.items()}
        state |= {key: (v, False, "collection") for key, v in collections.items()}
        return version, state

    def poll(self) -> None:
        result = self.snapshot()
        if result is None:
            return
        t = now_ms()
        version, state = result
        if self.state is not None:
            for kind, key in diff_states(self.state, state):
                self.recorder.add(self.name_, t, kind, [key], library_version=version)
        self.version, self.state = version, state


class LocalApiTrash(Poller):
    """Polls the trash list on every tick, because trashing does not advance the version."""

    name_ = "local_api_trash"
    interval = 1.0

    def __init__(self, recorder: Recorder, url: str) -> None:
        super().__init__(recorder)
        self.url = url + "/api/users/0/items/trash?format=keys"
        self.trash: set[str] | None = None
        self.request_ms: list[float] = []

    def poll(self) -> None:
        start = now_ms()
        status, _, body = http("GET", self.url, headers={"Zotero-API-Version": "3"})
        self.request_ms.append(now_ms() - start)
        if status != 200:
            raise RuntimeError(f"trash: HTTP {status}")
        trash = set(body.decode().split())
        t = now_ms()
        if self.trash is not None:
            for key in trash - self.trash:
                self.recorder.add(self.name_, t, "trashed", [key])
            for key in self.trash - trash:
                self.recorder.add(self.name_, t, "left_trash", [key])
        self.trash = trash


class DbCopy(Poller):
    name_ = "db_copy"
    interval = 1.0

    def __init__(self, recorder: Recorder, data_dir: Path) -> None:
        super().__init__(recorder)
        self.data_dir = data_dir
        self.files: list[tuple[int, int] | None] = []
        self.state: dict[str, tuple[Any, ...]] | None = None
        self.delete_log: set[str] = set()
        self.copy_ms: list[float] = []
        self.read_ms: list[float] = []
        self.busy = 0
        self.unchanged_polls = 0
        self.client_version = 0

    def file_state(self) -> list[tuple[int, int] | None]:
        states: list[tuple[int, int] | None] = []
        for name in DATABASE_FILES:
            try:
                stat = (self.data_dir / name).stat()
                states.append((stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                states.append(None)
        return states

    def read(self) -> tuple[int, dict[str, tuple[Any, ...]], set[str]]:
        """Copy and read the database the way the Zotero copy reader does; time both parts."""
        with tempfile.TemporaryDirectory(prefix="hw006-copy-") as temporary:
            start = now_ms()
            copy = zotero_db._copy_database(self.data_dir, Path(temporary), 3)
            copied = now_ms()
            conn = sqlite3.connect(copy)
            try:
                conn.row_factory = sqlite3.Row
                manifest, _ = zotero_db._read_copy(conn, datetime.now(UTC))
                client_version = conn.execute(
                    "SELECT clientVersion FROM libraries WHERE type = 'user'"
                ).fetchone()[0]
                delete_log = {row[0] for row in conn.execute("SELECT key FROM syncDeleteLog")}
            finally:
                conn.close()
            self.copy_ms.append(copied - start)
            self.read_ms.append(now_ms() - copied)
        state: dict[str, tuple[Any, ...]] = {}
        for records, kind in (
            (manifest.items, "item"),
            (manifest.attachments, "attachment"),
            (manifest.notes, "note"),
        ):
            for record in records:
                state[record.key] = (record.version, record.trashed, kind)
        for annotation in manifest.annotations:
            state[annotation.key] = (annotation.version, False, "annotation")
        for collection in manifest.collections:
            state[collection.key] = (
                collection.version,
                bool(getattr(collection, "trashed", False)),
                "collection",
            )
        return client_version, state, delete_log

    def poll(self) -> None:
        files = self.file_state()
        if files == self.files and self.state is not None:
            self.unchanged_polls += 1
            return
        try:
            client_version, state, delete_log = self.read()
        except HyperWikiError as error:
            if error.code == ErrorCode.ZOTERO_DATABASE_BUSY:
                self.busy += 1
                return
            raise
        t = now_ms()
        if self.state is not None:
            for kind, key in diff_states(self.state, state):
                self.recorder.add(self.name_, t, kind, [key], client_version=client_version)
            for key in delete_log - self.delete_log:
                self.recorder.add(self.name_, t, "delete_log", [key])
        self.files, self.state, self.delete_log = files, state, delete_log
        self.client_version = client_version


class Storage(Poller):
    name_ = "storage"
    interval = 1.0

    def __init__(self, recorder: Recorder, roots: dict[str, Path]) -> None:
        super().__init__(recorder)
        self.roots = roots
        self.state: dict[str, tuple[int, int]] | None = None

    def scan(self) -> dict[str, tuple[int, int]]:
        state = {}
        for label, root in self.roots.items():
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    stat = path.stat()
                    state[f"{label}/{path.relative_to(root).as_posix()}"] = (
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
        return state

    def poll(self) -> None:
        state = self.scan()
        t = now_ms()
        if self.state is not None:
            for name in state.keys() - self.state.keys():
                self.recorder.add(self.name_, t, "file_added", self.keys(name))
            for name in self.state.keys() - state.keys():
                self.recorder.add(self.name_, t, "file_removed", self.keys(name))
            for name in state.keys() & self.state.keys():
                if state[name] != self.state[name]:
                    self.recorder.add(self.name_, t, "file_changed", self.keys(name))
        self.state = state

    @staticmethod
    def keys(name: str) -> list[str]:
        """Storage files are matched by attachment key; linked files by file name."""
        parts = name.split("/")
        if parts[0] == "storage" and len(parts) > 2:
            return [parts[1]]
        return [parts[-1]]


class Notifier(threading.Thread):
    name_ = "notifier"

    def __init__(self, recorder: Recorder, log_path: Path) -> None:
        super().__init__(daemon=True)
        self.recorder = recorder
        self.log_path = log_path
        self.stopping = threading.Event()
        self.offset = 0
        self.events = 0

    def run(self) -> None:
        while not self.stopping.is_set():
            self.read()
            self.stopping.wait(0.05)
        self.read()

    def read(self) -> None:
        if not self.log_path.exists():
            return
        with self.log_path.open("rb") as log:
            log.seek(self.offset)
            chunk = log.read()
        complete = chunk[: chunk.rfind(b"\n") + 1]
        self.offset += len(complete)
        for line in complete.decode("utf-8").splitlines():
            event = json.loads(line)
            self.events += 1
            self.recorder.add(
                self.name_,
                float(event["t"]),
                f"{event['event']}:{event['type']}",
                [str(key) for key in event["keys"]],
            )


# --- scenario ------------------------------------------------------------------------------


def summarize(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 1),
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1),
        "max": round(ordered[-1], 1),
    }


class Scenario:
    def __init__(self, zotero: Zotero, recorder: Recorder, run_dir: Path, window: float) -> None:
        self.zotero = zotero
        self.recorder = recorder
        self.files = run_dir / "files"
        self.window = window
        self.keys: dict[str, str] = {}
        self.steps: list[dict[str, Any]] = []
        self.path_rows: list[dict[str, Any]] = []

    def step(
        self,
        name: str,
        apply: Callable[[], dict[str, Any]],
        *,
        match: Callable[[dict[str, Any]], list[str]] | None = None,
    ) -> dict[str, Any]:
        start = now_ms()
        result = apply()
        keys = match(result) if match else list(result.get("keys", []))
        time.sleep(self.window)
        seen = self.recorder.between(start, start + self.window * 1000)
        record: dict[str, Any] = {
            "step": name,
            "action_ms": round(result.get("done", start) - result.get("start", start), 1),
            "keys": len(keys),
        }
        for detector in DETECTORS:
            hits = [o for o in seen if o["detector"] == detector and set(o["keys"]) & set(keys)]
            record[detector] = (
                {
                    "latency_ms": round(min(o["t"] for o in hits) - start, 1),
                    "kinds": sorted({o["kind"] for o in hits}),
                    "keys_seen": len({k for o in hits for k in o["keys"]} & set(keys)),
                }
                if hits
                else None
            )
        self.steps.append(record)
        print(
            f"  {name}: "
            + ", ".join(
                f"{d}={'-' if record[d] is None else round(record[d]['latency_ms'])}"
                for d in DETECTORS
            ),
            flush=True,
        )
        return result

    def act(self, action: str, **args: Any) -> Callable[[], dict[str, Any]]:
        return lambda: self.zotero.act(action, **args)

    def run(self) -> None:
        z, k = self.zotero, self.keys
        make_pdf(self.files / "paper-en.pdf", "Hyper-Wiki change detection test paper")
        make_pdf(self.files / "论文-中文.pdf", "Chinese file name test")
        make_pdf(self.files / "linked-absolute.pdf", "Linked file outside the base directory")
        make_pdf(z.base / "sub" / "linked-relative.pdf", "Linked file inside the base directory")

        k["en"] = self.step(
            "create_item_en",
            self.act(
                "create_item",
                fields={
                    "title": "Detecting library changes without writing to Zotero",
                    "abstractNote": "A synthetic abstract for the HW-006 spike.",
                    "date": "2026-11-03",
                    "language": "en",
                },
                creators=[{"firstName": "Ada", "lastName": "Tester"}],
            ),
        )["keys"][0]
        k["zh"] = self.step(
            "create_item_zh",
            self.act(
                "create_item",
                fields={"title": "文献库变化检测的只读方法", "language": "zh-CN", "date": "2026"},
                creators=[{"lastName": "测试", "firstName": "研究者"}],
            ),
        )["keys"][0]
        k["pdf_en"] = self.step(
            "import_pdf",
            self.act("import_file", parentKey=k["en"], path=str(self.files / "paper-en.pdf")),
        )["keys"][0]
        k["pdf_zh"] = self.step(
            "import_pdf_chinese_name",
            self.act("import_file", parentKey=k["zh"], path=str(self.files / "论文-中文.pdf")),
        )["keys"][0]
        k["linked_abs"] = self.step(
            "link_pdf_absolute",
            self.act("link_file", parentKey=k["en"], path=str(self.files / "linked-absolute.pdf")),
        )["keys"][0]
        k["linked_rel"] = self.step(
            "link_pdf_in_base_dir",
            self.act(
                "link_file", parentKey=k["zh"], path=str(z.base / "sub" / "linked-relative.pdf")
            ),
        )["keys"][0]
        self.path_rows = path_resolution(z, k, self.files.parent)
        self.step(
            "edit_title",
            self.act(
                "edit_field", key=k["en"], field="title", value="Detecting library changes, revised"
            ),
        )
        k["note"] = self.step(
            "add_note",
            self.act("add_note", parentKey=k["en"], html="<p>A child note. 中文笔记。</p>"),
        )["keys"][0]
        self.step("edit_note", self.act("edit_note", key=k["note"], html="<p>Edited note.</p>"))
        k["annotation"] = self.step(
            "add_annotation",
            self.act("add_annotation", attachmentKey=k["pdf_en"], text="change detection test"),
        )["keys"][0]
        k["collection"] = self.step(
            "create_collection", self.act("create_collection", name="HW-006 collection")
        )["keys"][0]
        self.step(
            "add_to_collection",
            self.act("add_to_collection", key=k["en"], collectionKey=k["collection"]),
        )
        self.step("add_tag", self.act("add_tag", key=k["en"], tag="hw006"))
        self.step("rename_file", self.act("rename_file", key=k["pdf_en"], name="renamed.pdf"))
        self.step(
            "replace_file_outside_zotero",
            lambda: self.replace_file(k["pdf_en"]),
        )
        self.step(
            "replace_linked_file_outside_zotero",
            lambda: self.replace_linked(z.base / "sub" / "linked-relative.pdf"),
            match=lambda _: ["linked-relative.pdf", k["linked_rel"]],
        )
        self.step(
            "trash_item_with_children",
            self.act("trash", keys=[k["zh"]]),
            match=lambda _: [k["zh"], k["pdf_zh"], k["linked_rel"]],
        )
        self.step(
            "restore_item",
            self.act("restore", keys=[k["zh"]]),
            match=lambda _: [k["zh"], k["pdf_zh"], k["linked_rel"]],
        )
        self.step("trash_collection", self.act("trash_collection", key=k["collection"]))
        self.step("erase_collection", self.act("erase_collection", key=k["collection"]))
        k["tmp"] = self.step(
            "create_item_to_erase",
            self.act("create_item", fields={"title": "Erased without the trash"}),
        )["keys"][0]
        self.step("erase_item_directly", self.act("erase", keys=[k["tmp"]]))
        self.step(
            "trash_item_again",
            self.act("trash", keys=[k["en"]]),
            match=lambda _: [k["en"], k["pdf_en"], k["linked_abs"], k["note"]],
        )
        self.step(
            "empty_trash",
            self.act("empty_trash"),
            match=lambda r: [*r["keys"], k["pdf_en"], k["linked_abs"], k["note"], k["annotation"]],
        )
        bulk = self.step(
            "create_50_items_in_one_transaction",
            self.act("create_items_bulk", count=50, titlePrefix="Bulk item"),
        )
        k["bulk"] = ",".join(bulk["keys"])

    def replace_file(self, attachment_key: str) -> dict[str, Any]:
        start = now_ms()
        folder = self.zotero.data / "storage" / attachment_key
        target = next(p for p in folder.iterdir() if p.suffix == ".pdf")
        make_pdf(target, "Replaced outside Zotero")
        return {"keys": [attachment_key], "start": start, "done": now_ms()}

    def replace_linked(self, path: Path) -> dict[str, Any]:
        start = now_ms()
        make_pdf(path, "Linked file replaced outside Zotero")
        return {"keys": [path.name], "start": start, "done": now_ms()}


# --- checks --------------------------------------------------------------------------------


def try_direct_open(data_dir: Path) -> dict[str, Any]:
    """Try to read the live database directly, read-only, without waiting for locks."""
    before = sorted(p.name for p in data_dir.iterdir())
    uri = (data_dir / "zotero.sqlite").as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=0)
        try:
            count = conn.execute("SELECT count(*) FROM items").fetchone()[0]
            outcome = {"opened": True, "items": count}
        finally:
            conn.close()
    except sqlite3.Error as error:
        outcome = {"opened": False, "error": f"{type(error).__name__}: {error}"}
    after = sorted(p.name for p in data_dir.iterdir())
    outcome["files_created"] = sorted(set(after) - set(before))
    return outcome


def stress_copies(zotero: Zotero, db: DbCopy, seconds: float) -> dict[str, Any]:
    """Copy and read the database as fast as possible while Zotero writes in bulk."""
    results: dict[str, Any] = {
        "copies": 0,
        "busy": 0,
        "errors": [],
        "writes": 0,
        "write_errors": [],
    }
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            try:
                zotero.act("create_items_bulk", count=20, titlePrefix="Stress item")
                results["writes"] += 1
            except Exception as error:
                results["write_errors"].append(str(error))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    deadline = time.monotonic() + seconds
    probe = DbCopy(Recorder(), db.data_dir)
    # Count copy attempts: each attempt checks every database file twice (before and after).
    state_checks = [0]
    file_state = zotero_db._file_state

    def counting_file_state(path: Path) -> tuple[int, int] | None:
        state_checks[0] += 1
        return file_state(path)

    zotero_db._file_state = counting_file_state
    attempts: list[int] = []
    while time.monotonic() < deadline:
        state_checks[0] = 0
        try:
            probe.read()
            results["copies"] += 1
            attempts.append(state_checks[0] // (2 * len(DATABASE_FILES)))
        except HyperWikiError as error:
            if error.code == ErrorCode.ZOTERO_DATABASE_BUSY:
                results["busy"] += 1
            else:
                results["errors"].append(str(error.code))
        except sqlite3.Error as error:
            results["errors"].append(f"{type(error).__name__}: {error}")
    stop.set()
    thread.join()
    zotero_db._file_state = file_state
    results["copies_needing_a_retry"] = sum(1 for n in attempts if n > 1)
    results["copy_ms"] = summarize(probe.copy_ms)
    results["read_ms"] = summarize(probe.read_ms)
    return results


def path_resolution(zotero: Zotero, keys: dict[str, str], run_dir: Path) -> list[dict[str, Any]]:
    """Compare the copy reader's file resolution with Zotero's own, per attachment kind."""
    loaded = zotero_db.read_zotero_library(
        zotero.data, linked_file_roots=[run_dir / "files", zotero.base]
    )
    rows = []
    for label in ("pdf_zh", "linked_abs", "linked_rel"):
        key = keys[label]
        info = zotero.act("file_info", key=key)
        stored = info["attachmentPath"] or ""
        status, _, body = http(
            "GET",
            f"{zotero.url}/api/users/0/items/{key}/file/view/url",
            headers={"Zotero-API-Version": "3"},
        )
        file = loaded.files.get(key)
        rows.append(
            {
                "attachment": label,
                "stored_path": next(
                    (p for p in ("storage:", "attachments:") if stored.startswith(p)), "absolute"
                ),
                "zotero_resolves": bool(info["filePath"]),
                "local_api_file_url": status == 200 and body.startswith(b"file:"),
                "copy_reader": file.availability if file else "not listed",
                "copy_reader_problems": sorted(
                    {p.message for p in loaded.problems if p.key == key}
                ),
            }
        )
    return rows


def offline_catch_up(
    zotero: Zotero, recorder: Recorder, pollers: list[Poller], keys: dict[str, str]
) -> dict[str, Any]:
    """Pause the pollers, change the library, resume, and see what each one catches up on."""
    bulk = keys["bulk"].split(",")
    for poller in pollers:
        poller.paused.set()
    time.sleep(1.5)
    edited, trashed, erased = bulk[0], bulk[1], bulk[2]
    zotero.act("edit_field", key=edited, field="title", value="Edited while offline")
    zotero.act("trash", keys=[trashed])
    zotero.act("erase", keys=[erased])
    added = zotero.act("create_item", fields={"title": "Added while offline"})["keys"][0]
    expected = {"modified": edited, "trashed": trashed, "removed": erased, "added": added}
    resume = now_ms()
    for poller in pollers:
        poller.paused.clear()
    time.sleep(4)
    seen = recorder.between(resume, now_ms())
    outcome: dict[str, Any] = {}
    for poller in pollers:
        caught = {
            change: any(
                o["detector"] == poller.name_
                and key in o["keys"]
                and (o["kind"] == change or (change == "removed" and o["kind"] == "delete_log"))
                for o in seen
            )
            for change, key in expected.items()
        }
        outcome[poller.name_] = caught
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--zotero", type=Path, default=default_zotero())
    parser.add_argument("--port", type=int, default=23124)
    parser.add_argument("--window", type=float, default=4.0, help="seconds to watch each step")
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.json")
    args = parser.parse_args()

    private: Path = args.private.expanduser().resolve()
    if private.is_relative_to(REPO_ROOT):
        raise SystemExit("the private directory must be outside the repository")
    if args.port == 23119:
        raise SystemExit("use a port other than 23119, so the user's own Zotero is never hit")
    run_dir = private / datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True)
    zotero = Zotero(args.zotero, run_dir, args.port)
    zotero.prepare()
    print(f"run directory: {run_dir}", flush=True)

    started = now_ms()
    info = zotero.start()
    startup_s = (now_ms() - started) / 1000
    print(f"Zotero {info['zoteroVersion']} up in {startup_s:.1f} s", flush=True)
    summary: dict[str, Any] = {
        "measured": datetime.now().date().isoformat(),
        "zotero": info["zoteroVersion"],
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "startup_s": round(startup_s, 1),
        "window_s": args.window,
    }
    recorder = Recorder()
    notifier = Notifier(recorder, zotero.log_path)
    local_api = LocalApi(recorder, zotero.url)
    local_api_trash = LocalApiTrash(recorder, zotero.url)
    db_copy = DbCopy(recorder, zotero.data)
    storage = Storage(
        recorder,
        {"storage": zotero.data / "storage", "files": run_dir / "files", "base": zotero.base},
    )
    pollers: list[Poller] = [local_api, local_api_trash, db_copy, storage]
    try:
        time.sleep(3)  # let Zotero finish its own startup writes
        summary["direct_open_while_running"] = try_direct_open(zotero.data)
        for thread in (notifier, *pollers):
            thread.start()
        time.sleep(2)

        print("scenario:", flush=True)
        scenario = Scenario(zotero, recorder, run_dir, args.window)
        scenario.run()
        summary["steps"] = scenario.steps
        summary["path_resolution"] = scenario.path_rows
        print("offline catch-up", flush=True)
        summary["offline_catch_up"] = offline_catch_up(zotero, recorder, pollers, scenario.keys)
        print("stress: copying while Zotero writes", flush=True)
        db_copy.paused.set()
        summary["stress"] = stress_copies(zotero, db_copy, 20)
        db_copy.paused.clear()
        time.sleep(2)

        summary["local_api_requests_ms"] = summarize(local_api.request_ms)
        summary["local_api_not_modified"] = local_api.not_modified
        summary["local_api_trash_requests_ms"] = summarize(local_api_trash.request_ms)
        summary["db_copy"] = {
            "copy_ms": summarize(db_copy.copy_ms),
            "read_ms": summarize(db_copy.read_ms),
            "busy": db_copy.busy,
            "unchanged_polls": db_copy.unchanged_polls,
        }
        summary["poller_errors"] = {p.name_: p.errors[:5] for p in pollers if p.errors}
        summary["notifier_events"] = notifier.events
        data_files_running = sorted(p.name for p in zotero.data.iterdir() if p.is_file())
    finally:
        quit_s = zotero.stop()
        notifier.stopping.set()
        for poller in pollers:
            poller.stopping.set()

    summary["quit_s"] = None if quit_s is None else round(quit_s, 1)
    summary["data_files_while_running"] = data_files_running
    summary["data_files_after_quit"] = sorted(p.name for p in zotero.data.iterdir() if p.is_file())
    after = DbCopy(Recorder(), zotero.data)
    client_version, state, delete_log = after.read()
    summary["after_quit"] = {
        "copy_read_ok": True,
        "objects": len(state),
        "client_version": client_version,
        "delete_log_entries": len(delete_log),
        "local_api_reachable": False,
    }
    with contextlib.suppress(OSError):
        http("GET", zotero.url + "/api/", timeout=2)
        summary["after_quit"]["local_api_reachable"] = True

    (run_dir / "observations.json").write_text(
        json.dumps(recorder.observations, indent=1), encoding="utf-8"
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(
        json.dumps({k: v for k, v in summary.items() if k != "steps"}, indent=1, ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
