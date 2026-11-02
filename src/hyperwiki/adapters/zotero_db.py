"""Read the user library from a copy of Zotero's database.

Zotero's own ``zotero.sqlite`` is never opened with SQLite: opening it could take a lock
that blocks Zotero, or create files in its data directory. Instead the database and its
write-ahead log are copied byte for byte into a temporary directory, and the copy is read.
If the files change while they are copied (Zotero is writing), the copy is retried and then
refused. Attachment files are read in place under ``<data_dir>/storage``, only for hashing.

Versions come from Zotero's local ``clientVersion`` counter, which advances for local edits
even when the library is not synced (``version`` then stays 0; observed on Zotero 10.0.6).
The layout of ``zotero.sqlite`` is internal to Zotero, so the reader checks the tables and
columns it needs and fails with ``unsupported_zotero_database`` when they are missing.
"""

import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hyperwiki.adapters.snapshot import LoadedSnapshot, resolve_attachment_files
from hyperwiki.domain.common import EntityKind, Problem, parse_document
from hyperwiki.domain.library import (
    Annotation,
    AnnotationType,
    Attachment,
    Collection,
    Creator,
    Item,
    LibraryManifest,
    LibraryRef,
    LinkMode,
    Note,
)
from hyperwiki.errors import ErrorCode, HyperWikiError

ADAPTER = "zotero"
ZOTERO_DATABASE = "zotero.sqlite"
_COMPANION_SUFFIXES = ("-wal", "-journal")
_STORAGE_PREFIX = "storage:"
_LINK_MODES: dict[int, LinkMode] = {
    0: "imported_file",
    1: "imported_url",
    2: "linked_file",
    3: "linked_url",
}
_EMBEDDED_IMAGE = 4
_ANNOTATION_TYPES: dict[int, AnnotationType] = {
    1: "highlight",
    2: "note",
    3: "image",
    4: "ink",
    5: "underline",
    6: "text",
}
_NON_REGULAR_TYPES = frozenset({"attachment", "note", "annotation"})
_MULTIPART_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (.*)$", re.DOTALL)
_EXTRA_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]{0,63}):\s*(\S.*)$")

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "libraries": ("libraryID", "type", "clientVersion"),
    "settings": ("setting", "key", "value"),
    "items": ("itemID", "itemTypeID", "dateModified", "libraryID", "key", "clientVersion"),
    "itemTypes": ("itemTypeID", "typeName"),
    "itemData": ("itemID", "fieldID", "valueID"),
    "itemDataValues": ("valueID", "value"),
    "fields": ("fieldID", "fieldName"),
    "baseFieldMappingsCombined": ("itemTypeID", "baseFieldID", "fieldID"),
    "creators": ("creatorID", "firstName", "lastName", "fieldMode"),
    "itemCreators": ("itemID", "creatorID", "creatorTypeID", "orderIndex"),
    "creatorTypes": ("creatorTypeID", "creatorType"),
    "tags": ("tagID", "name"),
    "itemTags": ("itemID", "tagID"),
    "collections": (
        "collectionID",
        "collectionName",
        "parentCollectionID",
        "libraryID",
        "key",
        "clientVersion",
    ),
    "collectionItems": ("collectionID", "itemID"),
    "deletedItems": ("itemID",),
    "deletedCollections": ("collectionID",),
    "itemAttachments": ("itemID", "parentItemID", "linkMode", "contentType", "path"),
    "itemNotes": ("itemID", "parentItemID", "note"),
    "itemAnnotations": (
        "itemID",
        "parentItemID",
        "type",
        "text",
        "comment",
        "color",
        "pageLabel",
    ),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _copy_file(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)


def _file_state(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _copy_database(data_dir: Path, target_dir: Path, attempts: int) -> Path:
    """Copy the database and its journal files; retry while Zotero is writing to them."""
    names = [ZOTERO_DATABASE, *(ZOTERO_DATABASE + suffix for suffix in _COMPANION_SUFFIXES)]
    if not (data_dir / ZOTERO_DATABASE).is_file():
        raise HyperWikiError(
            ErrorCode.NOT_FOUND,
            f"no {ZOTERO_DATABASE} in the Zotero data directory",
            {"path": str(data_dir)},
        )
    for _ in range(attempts):
        for leftover in target_dir.iterdir():
            leftover.unlink()
        before = [_file_state(data_dir / name) for name in names]
        try:
            for name, state in zip(names, before, strict=True):
                if state is not None:
                    _copy_file(data_dir / name, target_dir / name)
        except FileNotFoundError:
            continue  # a journal file was removed mid-copy; Zotero is writing
        if [_file_state(data_dir / name) for name in names] == before:
            return target_dir / ZOTERO_DATABASE
    raise HyperWikiError(
        ErrorCode.ZOTERO_DATABASE_BUSY,
        "Zotero kept changing its database while it was being copied; try again shortly",
        {"attempts": attempts},
    )


def _unsupported(message: str, details: dict[str, Any] | None = None) -> HyperWikiError:
    return HyperWikiError(ErrorCode.UNSUPPORTED_ZOTERO_DATABASE, message, details)


def _userdata_version(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT version FROM version WHERE schema = 'userdata'").fetchone()
    except sqlite3.DatabaseError:
        return None
    return None if row is None else int(row[0])


def _check_database(conn: sqlite3.Connection) -> None:
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise _unsupported(f"{ZOTERO_DATABASE} cannot be read: {error}") from error
    if result != "ok":
        raise _unsupported(f"{ZOTERO_DATABASE} failed its integrity check", {"result": result})
    missing: list[str] = []
    for table, columns in REQUIRED_COLUMNS.items():
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing.extend(f"{table}.{column}" for column in columns if column not in present)
    if missing:
        raise _unsupported(
            "this Zotero database layout is not supported",
            {"missing": missing, "userdata_version": _userdata_version(conn)},
        )


def _zotero_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _date_text(value: str | None) -> str | None:
    """Zotero stores dates as ``YYYY-MM-DD original``; keep the original text."""
    if value is None:
        return None
    match = _MULTIPART_DATE.match(value)
    if match is None:
        return value
    original = match[2].strip()
    return original or re.sub(r"(-00)+$", "", match[1])


def _parse_extra(text: str | None) -> dict[str, str]:
    """``Key: value`` lines become entries; other lines are kept under ``text``."""
    entries: dict[str, str] = {}
    loose: list[str] = []
    for line in (text or "").splitlines():
        match = _EXTRA_LINE.match(line.strip())
        if match is None or match[2].startswith("//"):
            if line.strip():
                loose.append(line.strip())
            continue
        key, value = match[1].strip(), match[2].strip()
        entries[key] = f"{entries[key]}\n{value}" if key in entries else value
    if loose:
        joined = "\n".join(loose)
        entries["text"] = f"{entries['text']}\n{joined}" if "text" in entries else joined
    return entries


def _validation_message(error: ValidationError | ValueError) -> str:
    if isinstance(error, ValidationError):
        parts = [
            f"{'.'.join(str(part) for part in detail['loc']) or 'record'}: {detail['msg']}"
            for detail in error.errors(include_url=False)[:3]
        ]
        return "skipped: " + "; ".join(parts)
    return f"skipped: {error}"


class _Reader:
    """Builds manifest records from the copied database, collecting problems."""

    def __init__(self, conn: sqlite3.Connection, library_id: int) -> None:
        self.conn = conn
        self.library_id = library_id
        self.problems: list[Problem] = []
        self.keys: dict[int, str] = {}
        self.versions: dict[int, int] = {}
        self.modified: dict[int, str] = {}
        self.types: dict[int, str] = {}
        rows = conn.execute(
            """
            SELECT i.itemID, i.key, i.clientVersion, i.dateModified, t.typeName
            FROM items AS i JOIN itemTypes AS t ON t.itemTypeID = i.itemTypeID
            WHERE i.libraryID = ?
            ORDER BY i.itemID
            """,
            (library_id,),
        )
        for row in rows:
            self.keys[row["itemID"]] = row["key"]
            self.versions[row["itemID"]] = row["clientVersion"] or 0
            self.modified[row["itemID"]] = row["dateModified"]
            self.types[row["itemID"]] = row["typeName"]
        self.trashed = {row[0] for row in conn.execute("SELECT itemID FROM deletedItems")}
        self.fields = self._fields()

    def problem(self, entity: EntityKind, item_id: int, message: str) -> None:
        self.problems.append(
            Problem(
                code=ErrorCode.INVALID_DOCUMENT,
                entity=entity,
                key=self.keys[item_id],
                message=message,
            )
        )

    def _of_type(self, *type_names: str) -> list[int]:
        return [item_id for item_id, name in self.types.items() if name in type_names]

    def _fields(self) -> dict[int, dict[str, str]]:
        fields: dict[int, dict[str, str]] = defaultdict(dict)
        rows = self.conn.execute(
            """
            SELECT d.itemID, COALESCE(base.fieldName, f.fieldName) AS name, v.value
            FROM itemData AS d
            JOIN items AS i ON i.itemID = d.itemID
            JOIN fields AS f ON f.fieldID = d.fieldID
            JOIN itemDataValues AS v ON v.valueID = d.valueID
            LEFT JOIN baseFieldMappingsCombined AS m
                ON m.itemTypeID = i.itemTypeID AND m.fieldID = d.fieldID
            LEFT JOIN fields AS base ON base.fieldID = m.baseFieldID
            WHERE i.libraryID = ?
            """,
            (self.library_id,),
        )
        for row in rows:
            if row["value"] is not None:
                fields[row["itemID"]][row["name"]] = str(row["value"])
        return fields

    def collections(self) -> tuple[list[Collection], dict[int, str]]:
        rows = {
            row["collectionID"]: row
            for row in self.conn.execute(
                """
                SELECT collectionID, key, clientVersion, collectionName, parentCollectionID
                FROM collections WHERE libraryID = ?
                """,
                (self.library_id,),
            )
        }
        trashed = {row[0] for row in self.conn.execute("SELECT * FROM deletedCollections")}
        included: dict[int, Collection] = {}
        pending = [cid for cid in rows if cid not in trashed]
        while pending:
            progressed = False
            for cid in list(pending):
                parent = rows[cid]["parentCollectionID"]
                if parent is not None and parent not in included:
                    if parent not in rows or parent in trashed or parent not in pending:
                        pending.remove(cid)  # under a trashed, skipped, or missing parent
                        progressed = True
                    continue
                pending.remove(cid)
                progressed = True
                row = rows[cid]
                try:
                    included[cid] = Collection(
                        key=row["key"],
                        version=row["clientVersion"] or 0,
                        name=row["collectionName"],
                        parent_key=None if parent is None else included[parent].key,
                    )
                except ValidationError as error:
                    self.problems.append(
                        Problem(
                            code=ErrorCode.INVALID_DOCUMENT,
                            entity="collection",
                            key=str(row["key"]),
                            message=_validation_message(error),
                        )
                    )
            if not progressed:
                break  # a parent cycle; leave the rest out
        return list(included.values()), {cid: c.key for cid, c in included.items()}

    def _creators(self) -> dict[int, list[tuple[str, str | None, str | None, int]]]:
        creators: dict[int, list[tuple[str, str | None, str | None, int]]] = defaultdict(list)
        rows = self.conn.execute(
            """
            SELECT ic.itemID, ct.creatorType, c.firstName, c.lastName, c.fieldMode
            FROM itemCreators AS ic
            JOIN creators AS c ON c.creatorID = ic.creatorID
            JOIN creatorTypes AS ct ON ct.creatorTypeID = ic.creatorTypeID
            ORDER BY ic.itemID, ic.orderIndex
            """
        )
        for row in rows:
            creators[row["itemID"]].append(
                (row["creatorType"], row["firstName"], row["lastName"], row["fieldMode"] or 0)
            )
        return creators

    def _creator(
        self, item_id: int, creator_type: str, first: str | None, last: str | None, mode: int
    ) -> Creator | None:
        first, last = (first or "").strip() or None, (last or "").strip() or None
        if mode == 1 or (last is None and first is not None):
            name = last or first
            if name is not None:
                return Creator(creator_type=creator_type, name=name)
        elif last is not None:
            return Creator(creator_type=creator_type, first_name=first, last_name=last)
        self.problem("item", item_id, f"skipped a {creator_type} with no name")
        return None

    def items(self, collection_keys: dict[int, str]) -> list[Item]:
        creators = self._creators()
        tags: dict[int, set[str]] = defaultdict(set)
        for item_id, name in self.conn.execute(
            "SELECT it.itemID, t.name FROM itemTags AS it JOIN tags AS t ON t.tagID = it.tagID"
        ):
            tags[item_id].add(name)
        memberships: dict[int, set[str]] = defaultdict(set)
        for collection_id, item_id in self.conn.execute(
            "SELECT collectionID, itemID FROM collectionItems"
        ):
            if collection_id in collection_keys:
                memberships[item_id].add(collection_keys[collection_id])
        items: list[Item] = []
        regular = [i for i, name in self.types.items() if name not in _NON_REGULAR_TYPES]
        for item_id in regular:
            fields = self.fields.get(item_id, {})
            try:
                item_creators = [
                    creator
                    for spec in creators.get(item_id, [])
                    if (creator := self._creator(item_id, *spec)) is not None
                ]
                items.append(
                    Item(
                        key=self.keys[item_id],
                        version=self.versions[item_id],
                        item_type=self.types[item_id],
                        title=fields.get("title", ""),
                        creators=item_creators,
                        date=_date_text(fields.get("date")),
                        doi=fields.get("DOI"),
                        url=fields.get("url"),
                        abstract=fields.get("abstractNote"),
                        language=fields.get("language"),
                        tags=sorted(tags.get(item_id, set())),
                        collection_keys=sorted(memberships.get(item_id, set())),
                        extra=_parse_extra(fields.get("extra")),
                        trashed=item_id in self.trashed,
                        date_modified=_zotero_time(self.modified[item_id]),
                    )
                )
            except (ValidationError, ValueError) as error:
                self.problem("item", item_id, _validation_message(error))
        return items

    def _parent_key(
        self, entity: EntityKind, item_id: int, parent_id: int | None, allowed: set[str]
    ) -> tuple[bool, str | None]:
        """Return (ok, parent key); a parent that was not imported skips the child."""
        if parent_id is None:
            return True, None
        parent_key = self.keys.get(parent_id)
        if parent_key is None or parent_key not in allowed:
            self.problem(entity, item_id, "skipped: its parent was not imported")
            return False, None
        return True, parent_key

    def attachments(self, item_keys: set[str]) -> list[Attachment]:
        attachments: list[Attachment] = []
        rows = self.conn.execute(
            """
            SELECT a.itemID, a.parentItemID, a.linkMode, a.contentType, a.path
            FROM itemAttachments AS a JOIN items AS i ON i.itemID = a.itemID
            WHERE i.libraryID = ?
            ORDER BY a.itemID
            """,
            (self.library_id,),
        )
        for row in rows:
            item_id = row["itemID"]
            if row["linkMode"] == _EMBEDDED_IMAGE:
                continue  # an image inside a note, not a paper attachment
            link_mode = _LINK_MODES.get(row["linkMode"])
            if link_mode is None:
                self.problem("attachment", item_id, f"skipped: link mode {row['linkMode']}")
                continue
            ok, parent_key = self._parent_key("attachment", item_id, row["parentItemID"], item_keys)
            if not ok:
                continue
            key = self.keys[item_id]
            path: str | None = row["path"]
            if link_mode in ("imported_file", "imported_url"):
                if path is None or not path.startswith(_STORAGE_PREFIX):
                    self.problem("attachment", item_id, "skipped: no stored file path")
                    continue
                path = f"{key}/{path.removeprefix(_STORAGE_PREFIX)}"
            elif link_mode == "linked_url":
                path = None
            try:
                attachments.append(
                    Attachment(
                        key=key,
                        version=self.versions[item_id],
                        parent_key=parent_key,
                        content_type=row["contentType"] or "",
                        link_mode=link_mode,
                        path=path,
                        url=self.fields.get(item_id, {}).get("url"),
                        trashed=item_id in self.trashed,
                        date_modified=_zotero_time(self.modified[item_id]),
                    )
                )
            except (ValidationError, ValueError) as error:
                self.problem("attachment", item_id, _validation_message(error))
        return attachments

    def notes(self, item_keys: set[str]) -> list[Note]:
        notes: list[Note] = []
        note_ids = set(self._of_type("note"))
        rows = self.conn.execute("SELECT itemID, parentItemID, note FROM itemNotes")
        for row in rows:
            item_id = row["itemID"]
            if item_id not in note_ids:
                continue  # notes stored on attachments belong to the attachment
            ok, parent_key = self._parent_key("note", item_id, row["parentItemID"], item_keys)
            if not ok:
                continue
            try:
                notes.append(
                    Note(
                        key=self.keys[item_id],
                        version=self.versions[item_id],
                        parent_key=parent_key,
                        html=row["note"] or "",
                        trashed=item_id in self.trashed,
                        date_modified=_zotero_time(self.modified[item_id]),
                    )
                )
            except (ValidationError, ValueError) as error:
                self.problem("note", item_id, _validation_message(error))
        return sorted(notes, key=lambda note: note.key)

    def annotations(self, attachment_keys: set[str]) -> list[Annotation]:
        annotations: list[Annotation] = []
        rows = self.conn.execute(
            """
            SELECT an.itemID, an.parentItemID, an.type, an.text, an.comment, an.color,
                an.pageLabel
            FROM itemAnnotations AS an JOIN items AS i ON i.itemID = an.itemID
            WHERE i.libraryID = ?
            ORDER BY an.itemID
            """,
            (self.library_id,),
        )
        for row in rows:
            item_id = row["itemID"]
            if item_id in self.trashed:
                continue
            annotation_type = _ANNOTATION_TYPES.get(row["type"])
            if annotation_type is None:
                self.problem("annotation", item_id, f"skipped: annotation type {row['type']}")
                continue
            parent_key = self.keys.get(row["parentItemID"])
            if parent_key is None or parent_key not in attachment_keys:
                self.problem("annotation", item_id, "skipped: its attachment was not imported")
                continue
            try:
                annotations.append(
                    Annotation(
                        key=self.keys[item_id],
                        version=self.versions[item_id],
                        parent_key=parent_key,
                        annotation_type=annotation_type,
                        text=row["text"],
                        comment=row["comment"],
                        page_label=row["pageLabel"],
                        color=row["color"],
                        date_modified=_zotero_time(self.modified[item_id]),
                    )
                )
            except (ValidationError, ValueError) as error:
                self.problem("annotation", item_id, _validation_message(error))
        return annotations


def _keys(records: Iterable[Item | Attachment]) -> set[str]:
    return {record.key for record in records}


def _read_copy(conn: sqlite3.Connection, now: datetime) -> tuple[LibraryManifest, list[Problem]]:
    _check_database(conn)
    library = conn.execute(
        "SELECT libraryID, clientVersion FROM libraries WHERE type = 'user' ORDER BY libraryID"
    ).fetchone()
    if library is None:
        raise _unsupported("the Zotero database has no user library")
    user_key = conn.execute(
        "SELECT value FROM settings WHERE setting = 'account' AND key = 'localUserKey'"
    ).fetchone()
    reader = _Reader(conn, library["libraryID"])
    collections, collection_keys = reader.collections()
    items = reader.items(collection_keys)
    attachments = reader.attachments(_keys(items))
    notes = reader.notes(_keys(items))
    annotations = reader.annotations(_keys(attachments))
    manifest = parse_document(
        LibraryManifest,
        {
            "schema_version": 1,
            "adapter": ADAPTER,
            "library": LibraryRef(
                id=f"local:{user_key[0]}" if user_key and user_key[0] else "local",
                kind="user",
                name="My Library",
            ),
            "snapshot_id": f"zotero-{library['clientVersion'] or 0}",
            "created_at": now,
            "completeness": "complete",
            "collections": collections,
            "items": items,
            "attachments": attachments,
            "notes": notes,
            "annotations": annotations,
        },
    )
    return manifest, reader.problems


def read_zotero_library(
    data_dir: Path,
    *,
    linked_file_roots: Sequence[Path] = (),
    now: Callable[[], datetime] = utc_now,
    copy_attempts: int = 3,
) -> LoadedSnapshot:
    """Read the user library of the Zotero data directory ``data_dir`` without touching it."""
    created_at = now()
    with tempfile.TemporaryDirectory(prefix="hyperwiki-zotero-") as temporary:
        copy = _copy_database(data_dir, Path(temporary), copy_attempts)
        conn = sqlite3.connect(copy)
        try:
            conn.row_factory = sqlite3.Row
            manifest, problems = _read_copy(conn, created_at)
        finally:
            conn.close()
    storage_root = (data_dir / "storage").resolve()
    files, file_problems = resolve_attachment_files(
        manifest.attachments, storage_root, linked_file_roots
    )
    problems.extend(file_problems)
    problems.sort(key=lambda problem: (problem.entity, problem.key))
    return LoadedSnapshot(
        directory=data_dir.resolve(), manifest=manifest, files=files, problems=problems
    )
