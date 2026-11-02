"""SQLite connections and forward-only schema migrations tracked in ``PRAGMA user_version``."""

import re
import sqlite3
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import NamedTuple

from hyperwiki.errors import ErrorCode, HyperWikiError

_MIGRATION_FILE = re.compile(r"^(\d{4})_(\w+)\.sql$")


class Migration(NamedTuple):
    version: int
    name: str
    sql: str


def _load_migrations() -> tuple[Migration, ...]:
    folder = resources.files("hyperwiki.storage") / "migrations"
    found: list[Migration] = []
    for entry in folder.iterdir():
        match = _MIGRATION_FILE.match(entry.name)
        if match:
            found.append(Migration(int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [migration.version for migration in found]
    if versions != list(range(1, len(found) + 1)):
        raise RuntimeError(f"migration versions must be 1..N without gaps, found {versions}")
    return tuple(found)


MIGRATIONS: tuple[Migration, ...] = _load_migrations()


def latest_version(migrations: Sequence[Migration] = MIGRATIONS) -> int:
    return max((migration.version for migration in migrations), default=0)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        conn.close()
        raise
    return conn


def _user_version(conn: sqlite3.Connection) -> int:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def _statements(sql: str) -> list[str]:
    """Split a migration script into complete statements, keeping trigger bodies whole."""
    statements: list[str] = []
    buffer = ""
    for piece in sql.split(";"):
        buffer += piece + ";"
        if sqlite3.complete_statement(buffer):
            if buffer.strip() != ";":
                statements.append(buffer.strip())
            buffer = ""
    buffer = buffer.removesuffix(";")
    if buffer.strip():
        raise ValueError(f"incomplete SQL statement in migration: {buffer.strip()[:80]!r}")
    return statements


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> int:
    """Apply pending migrations, each atomically with its version bump.

    Each migration takes the write lock first and re-reads the schema version, so two
    processes opening a new database at the same time apply every migration exactly once.
    """
    current = _user_version(conn)
    latest = latest_version(migrations)
    if current > latest:
        raise HyperWikiError(
            ErrorCode.UNSUPPORTED_DATABASE_VERSION,
            "the database was written by a newer Hyper-Wiki; upgrade Hyper-Wiki to open it",
            {"found": current, "latest": latest},
        )
    for migration in sorted(migrations):
        if migration.version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _user_version(conn)
            if migration.version > current:
                for statement in _statements(migration.sql):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(migration.version)}")
                current = migration.version
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return current


def open_database(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn
