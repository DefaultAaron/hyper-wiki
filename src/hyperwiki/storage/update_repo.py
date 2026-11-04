"""Repository for update settings, collection exclusion, and the pending list (HW-013)."""

import json
import sqlite3
from collections.abc import Collection
from datetime import datetime

from pydantic import NonNegativeInt

from hyperwiki.domain.common import Contract, ZoteroKey
from hyperwiki.domain.updates import UpdateSettings
from hyperwiki.errors import ErrorCode, HyperWikiError
from hyperwiki.storage.database import from_db_time, to_db_time

_LAST_FULL_CHECK = "last_full_check_at"


def _read(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        row["name"]: json.loads(row["value_json"])
        for row in conn.execute("SELECT name, value_json FROM workspace_settings")
    }


def _write(conn: sqlite3.Connection, values: dict[str, object]) -> None:
    with conn:
        conn.executemany(
            """
            INSERT INTO workspace_settings (name, value_json) VALUES (?, ?)
            ON CONFLICT (name) DO UPDATE SET value_json = excluded.value_json
            """,
            [(name, json.dumps(value)) for name, value in values.items()],
        )


def get_update_settings(conn: sqlite3.Connection) -> UpdateSettings:
    stored = _read(conn)
    return UpdateSettings.model_validate(
        {name: stored[name] for name in UpdateSettings.model_fields if name in stored}
    )


def set_update_settings(
    conn: sqlite3.Connection,
    *,
    automatic: bool | None = None,
    schedule: str | None = None,
    paused: bool | None = None,
) -> UpdateSettings:
    """Change the given settings, validated, and return them all."""
    changes = {
        name: value
        for name, value in (("automatic", automatic), ("schedule", schedule), ("paused", paused))
        if value is not None
    }
    settings = UpdateSettings.model_validate({**get_update_settings(conn).model_dump(), **changes})
    _write(conn, {name: getattr(settings, name) for name in changes})
    return settings


def last_full_check(conn: sqlite3.Connection) -> datetime | None:
    value = _read(conn).get(_LAST_FULL_CHECK)
    return None if value is None else from_db_time(str(value))


def record_full_check(conn: sqlite3.Connection, at: datetime) -> None:
    _write(conn, {_LAST_FULL_CHECK: to_db_time(at)})


def _libraries_with_collection(conn: sqlite3.Connection, key: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT library_id FROM collections
        WHERE key = ? AND deleted_at IS NULL ORDER BY library_id
        """,
        (key,),
    )
    return [row[0] for row in rows]


def exclude_collection(conn: sqlite3.Connection, key: str) -> list[str]:
    """Exclude a live collection, with its subcollections, in every library that has it."""
    libraries = _libraries_with_collection(conn, key)
    if not libraries:
        raise HyperWikiError(
            ErrorCode.NOT_FOUND, "no such collection in the workspace", {"collection_key": key}
        )
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO excluded_collections (library_id, collection_key) VALUES (?, ?)",
            [(library_id, key) for library_id in libraries],
        )
    return libraries


def include_collection(conn: sqlite3.Connection, key: str) -> list[str]:
    """Remove an exclusion; returns the libraries it was removed from."""
    libraries = [
        row[0]
        for row in conn.execute(
            "SELECT library_id FROM excluded_collections WHERE collection_key = ? "
            "ORDER BY library_id",
            (key,),
        )
    ]
    if not libraries:
        raise HyperWikiError(
            ErrorCode.NOT_FOUND, "the collection is not excluded", {"collection_key": key}
        )
    with conn:
        conn.execute("DELETE FROM excluded_collections WHERE collection_key = ?", (key,))
    return libraries


def excluded_collections(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Excluded collection keys by library ID."""
    excluded: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT library_id, collection_key FROM excluded_collections "
        "ORDER BY library_id, collection_key"
    ):
        excluded.setdefault(row[0], []).append(row[1])
    return excluded


# Live collections that are excluded, directly or through an excluded ancestor.
_EXCLUDED_COLLECTIONS = """
WITH RECURSIVE excluded(id, key) AS (
    SELECT c.id, c.key FROM collections AS c
    JOIN excluded_collections AS x
        ON x.library_id = c.library_id AND x.collection_key = c.key
    WHERE c.library_id = :library AND c.deleted_at IS NULL
    UNION
    SELECT c.id, c.key FROM collections AS c
    JOIN excluded AS e ON c.parent_key = e.key
    WHERE c.library_id = :library AND c.deleted_at IS NULL
),
memberships AS (
    SELECT ic.item_id, c.id IN (SELECT id FROM excluded) AS is_excluded
    FROM item_collections AS ic
    JOIN collections AS c ON c.id = ic.collection_id AND c.deleted_at IS NULL
)
"""


def excluded_item_ids(
    conn: sqlite3.Connection, library_id: str, item_ids: Collection[str] | None = None
) -> set[str]:
    """Live items whose live collections are all excluded; unfiled items never are.

    ``item_ids`` limits the check to those items, for callers that need only a few.
    """
    params: dict[str, object] = {"library": library_id}
    limit = ""
    if item_ids is not None:
        if not item_ids:
            return set()
        names = [f"i{index}" for index in range(len(item_ids))]
        params.update(zip(names, item_ids, strict=True))
        limit = f"AND i.id IN ({', '.join(':' + name for name in names)})"
    rows = conn.execute(
        f"""
        {_EXCLUDED_COLLECTIONS}
        SELECT i.id FROM items AS i
        JOIN memberships AS m ON m.item_id = i.id
        WHERE i.library_id = :library AND i.deleted_at IS NULL {limit}
        GROUP BY i.id
        HAVING MIN(m.is_excluded) = 1
        """,
        params,
    )
    return {row[0] for row in rows}


def _placeholders(name: str, values: Collection[str]) -> tuple[str, dict[str, str]]:
    names = [f"{name}{index}" for index in range(len(values))]
    return ", ".join(":" + n for n in names), dict(zip(names, sorted(values), strict=True))


def selected_attachment_ids(
    conn: sqlite3.Connection, item_keys: Collection[str], collection_keys: Collection[str]
) -> tuple[set[str], list[str], list[str]]:
    """Live attachments of the chosen items, standalone attachments, and collections (with
    their subcollections), in every library; also the item and collection keys not found."""
    attachments: set[str] = set()
    missing_items: list[str] = []
    missing_collections: list[str] = []
    if item_keys:
        marks, params = _placeholders("k", item_keys)
        found = {
            row[0]
            for row in conn.execute(
                f"""
                SELECT key FROM items WHERE key IN ({marks}) AND deleted_at IS NULL
                UNION
                SELECT key FROM attachments WHERE key IN ({marks}) AND deleted_at IS NULL
                """,
                params,
            )
        }
        missing_items = sorted(set(item_keys) - found)
        attachments |= {
            row[0]
            for row in conn.execute(
                f"""
                SELECT a.id FROM attachments AS a
                LEFT JOIN items AS i ON i.id = a.parent_item_id
                WHERE a.deleted_at IS NULL AND i.deleted_at IS NULL
                    AND (a.key IN ({marks}) OR i.key IN ({marks}))
                """,
                params,
            )
        }
    if collection_keys:
        marks, params = _placeholders("c", collection_keys)
        found = {
            row[0]
            for row in conn.execute(
                f"SELECT key FROM collections WHERE key IN ({marks}) AND deleted_at IS NULL",
                params,
            )
        }
        missing_collections = sorted(set(collection_keys) - found)
        attachments |= {
            row[0]
            for row in conn.execute(
                f"""
                WITH RECURSIVE chosen(id, library_id, key) AS (
                    SELECT id, library_id, key FROM collections
                    WHERE key IN ({marks}) AND deleted_at IS NULL
                    UNION
                    SELECT c.id, c.library_id, c.key FROM collections AS c
                    JOIN chosen AS p ON c.library_id = p.library_id AND c.parent_key = p.key
                    WHERE c.deleted_at IS NULL
                )
                SELECT a.id FROM attachments AS a
                JOIN items AS i ON i.id = a.parent_item_id AND i.deleted_at IS NULL
                JOIN item_collections AS ic ON ic.item_id = i.id
                WHERE a.deleted_at IS NULL AND ic.collection_id IN (SELECT id FROM chosen)
                """,
                params,
            )
        }
    return attachments, missing_items, missing_collections


class PendingItem(Contract):
    """Queued or running work for one item (or one standalone attachment)."""

    library_id: str
    item_key: ZoteroKey | None
    title: str | None
    attachment_keys: list[ZoteroKey]
    jobs: NonNegativeInt


def pending_items(conn: sqlite3.Connection) -> list[PendingItem]:
    """Queued or leased jobs on attachment versions, grouped by parent item."""
    groups: dict[tuple[str, str], PendingItem] = {}
    for row in conn.execute(
        """
        SELECT a.library_id, a.key AS attachment_key, i.key AS item_key, i.title
        FROM jobs AS j
        JOIN attachment_versions AS v ON v.id = j.subject
        JOIN attachments AS a ON a.id = v.attachment_id
        LEFT JOIN items AS i ON i.id = a.parent_item_id
        WHERE j.state IN ('queued', 'leased')
        ORDER BY a.library_id, i.key, a.key
        """
    ):
        group_key = (row["library_id"], row["item_key"] or row["attachment_key"])
        group = groups.get(group_key) or PendingItem(
            library_id=row["library_id"],
            item_key=row["item_key"],
            title=row["title"],
            attachment_keys=[],
            jobs=0,
        )
        keys = group.attachment_keys
        if row["attachment_key"] not in keys:
            keys = [*keys, row["attachment_key"]]
        groups[group_key] = group.model_copy(
            update={"attachment_keys": keys, "jobs": group.jobs + 1}
        )
    return list(groups.values())
