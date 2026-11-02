"""Build a synthetic Zotero data directory with the tables and columns the reader uses.

Table and column names follow Zotero 10.0.6's ``zotero.sqlite`` (observed 2026-11-03); only
the columns Hyper-Wiki reads, plus a few neighbours, are created. All content is synthetic.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE version (schema TEXT PRIMARY KEY, version INT NOT NULL);
CREATE TABLE settings (setting TEXT, key TEXT, value, PRIMARY KEY (setting, key));
CREATE TABLE libraries (
    libraryID INTEGER PRIMARY KEY, type TEXT NOT NULL, editable INT NOT NULL DEFAULT 1,
    filesEditable INT NOT NULL DEFAULT 1, version INT NOT NULL DEFAULT 0,
    storageVersion INT NOT NULL DEFAULT 0, lastSync INT NOT NULL DEFAULT 0,
    archived INT NOT NULL DEFAULT 0, isAdmin INT, clientVersion INT NOT NULL DEFAULT 0
);
CREATE TABLE itemTypes (
    itemTypeID INTEGER PRIMARY KEY, typeName TEXT, templateItemTypeID INT, display INT
);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT, fieldFormatID INT);
CREATE TABLE baseFieldMappingsCombined (itemTypeID INT, baseFieldID INT, fieldID INT);
CREATE TABLE items (
    itemID INTEGER PRIMARY KEY, itemTypeID INT NOT NULL,
    dateAdded TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    libraryID INT NOT NULL, key TEXT NOT NULL, version INT NOT NULL DEFAULT 0,
    synced INT NOT NULL DEFAULT 0, clientVersion INT NOT NULL DEFAULT 0,
    UNIQUE (libraryID, key)
);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value UNIQUE, valueNormalized TEXT);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID, PRIMARY KEY (itemID, fieldID));
CREATE TABLE creators (
    creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INT,
    firstNameNormalized TEXT, lastNameNormalized TEXT
);
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE itemCreators (
    itemID INT NOT NULL, creatorID INT NOT NULL, creatorTypeID INT NOT NULL DEFAULT 1,
    orderIndex INT NOT NULL DEFAULT 0, PRIMARY KEY (itemID, creatorID, creatorTypeID, orderIndex)
);
CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, nameNormalized TEXT);
CREATE TABLE itemTags (itemID INT NOT NULL, tagID INT NOT NULL, type INT NOT NULL,
    PRIMARY KEY (itemID, tagID));
CREATE TABLE collections (
    collectionID INTEGER PRIMARY KEY, collectionName TEXT NOT NULL, parentCollectionID INT,
    clientDateModified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, libraryID INT NOT NULL,
    key TEXT NOT NULL, version INT NOT NULL DEFAULT 0, synced INT NOT NULL DEFAULT 0,
    clientVersion INT NOT NULL DEFAULT 0, UNIQUE (libraryID, key)
);
CREATE TABLE collectionItems (collectionID INT NOT NULL, itemID INT NOT NULL,
    orderIndex INT NOT NULL DEFAULT 0, PRIMARY KEY (collectionID, itemID));
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE deletedCollections (
    collectionID INTEGER PRIMARY KEY, dateDeleted DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE itemAttachments (
    itemID INTEGER PRIMARY KEY, parentItemID INT, linkMode INT, contentType TEXT,
    charsetID INT, path TEXT, syncState INT DEFAULT 0, storageModTime INT, storageHash TEXT,
    lastProcessedModificationTime INT, lastRead INT
);
CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT, title TEXT);
CREATE TABLE itemAnnotations (
    itemID INTEGER PRIMARY KEY, parentItemID INT NOT NULL, type INTEGER NOT NULL,
    authorName TEXT, text TEXT, comment TEXT, color TEXT, pageLabel TEXT, sortIndex TEXT,
    position TEXT, isExternal INT NOT NULL DEFAULT 0
);
"""

ITEM_TYPES = (
    "journalArticle",
    "conferencePaper",
    "book",
    "case",
    "attachment",
    "note",
    "annotation",
)
FIELDS = ("title", "date", "DOI", "url", "abstractNote", "language", "extra", "caseName")
MODIFIED = "2026-04-02 03:34:21"


@dataclass
class ZoteroDb:
    """A Zotero data directory under construction; ``conn`` writes ``zotero.sqlite``."""

    data_dir: Path
    conn: sqlite3.Connection
    library_id: int = 1
    _ids: dict[str, int] = field(default_factory=dict)

    @classmethod
    def create(cls, data_dir: Path, *, client_version: int = 20) -> "ZoteroDb":
        data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(data_dir / "zotero.sqlite")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO version VALUES ('userdata', 130)")
        conn.execute("INSERT INTO settings VALUES ('account', 'localUserKey', 'AbCdEfGh')")
        conn.execute(
            "INSERT INTO libraries (libraryID, type, clientVersion) VALUES (1, 'user', ?)",
            (client_version,),
        )
        conn.execute("INSERT INTO libraries (libraryID, type) VALUES (2, 'group')")
        conn.executemany(
            "INSERT INTO itemTypes (itemTypeID, typeName) VALUES (?, ?)",
            list(enumerate(ITEM_TYPES, start=1)),
        )
        conn.executemany(
            "INSERT INTO fields (fieldID, fieldName) VALUES (?, ?)",
            list(enumerate(FIELDS, start=1)),
        )
        conn.execute(
            "INSERT INTO baseFieldMappingsCombined VALUES (?, ?, ?)",
            (ITEM_TYPES.index("case") + 1, FIELDS.index("title") + 1, FIELDS.index("caseName") + 1),
        )
        conn.executemany("INSERT INTO creatorTypes VALUES (?, ?)", [(1, "author"), (2, "editor")])
        conn.commit()
        return cls(data_dir, conn)

    def id(self, key: str) -> int:
        return self._ids[key]

    def _add(
        self,
        key: str,
        item_type: str,
        *,
        client_version: int = 1,
        library_id: int | None = None,
        modified: str = MODIFIED,
        trashed: bool = False,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO items (itemTypeID, dateModified, libraryID, key, clientVersion)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ITEM_TYPES.index(item_type) + 1,
                modified,
                library_id or self.library_id,
                key,
                client_version,
            ),
        )
        item_id = cursor.lastrowid
        assert item_id is not None
        self._ids[key] = item_id
        if trashed:
            self.conn.execute("INSERT INTO deletedItems (itemID) VALUES (?)", (item_id,))
        return item_id

    def _set_field(self, item_id: int, name: str, value: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO itemDataValues (value) VALUES (?)", (value,))
        value_id = self.conn.execute(
            "SELECT valueID FROM itemDataValues WHERE value = ?", (value,)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO itemData VALUES (?, ?, ?)", (item_id, FIELDS.index(name) + 1, value_id)
        )

    def collection(
        self,
        key: str,
        name: str,
        *,
        parent: str | None = None,
        trashed: bool = False,
        client_version: int = 1,
    ) -> None:
        cursor = self.conn.execute(
            """
            INSERT INTO collections
                (collectionName, parentCollectionID, libraryID, key, clientVersion)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                None if parent is None else self._ids[parent],
                self.library_id,
                key,
                client_version,
            ),
        )
        collection_id = cursor.lastrowid
        assert collection_id is not None
        self._ids[key] = collection_id
        if trashed:
            self.conn.execute(
                "INSERT INTO deletedCollections (collectionID) VALUES (?)", (collection_id,)
            )

    def item(
        self,
        key: str,
        item_type: str = "journalArticle",
        *,
        fields: dict[str, str] | None = None,
        creators: Sequence[tuple[str, str | None, str | None, int]] = (),
        tags: Sequence[str] = (),
        collections: Sequence[str] = (),
        client_version: int = 1,
        library_id: int | None = None,
        trashed: bool = False,
    ) -> None:
        """``creators`` holds (creator type, first name, last name, field mode)."""
        item_id = self._add(
            key,
            item_type,
            client_version=client_version,
            library_id=library_id,
            trashed=trashed,
        )
        for name, value in (fields or {}).items():
            self._set_field(item_id, name, value)
        for index, (creator_type, first, last, mode) in enumerate(creators):
            creator_id = self.conn.execute(
                "INSERT INTO creators (firstName, lastName, fieldMode) VALUES (?, ?, ?)",
                (first, last, mode),
            ).lastrowid
            type_id = 1 if creator_type == "author" else 2
            self.conn.execute(
                "INSERT INTO itemCreators VALUES (?, ?, ?, ?)",
                (item_id, creator_id, type_id, index),
            )
        for tag in tags:
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            tag_id = self.conn.execute("SELECT tagID FROM tags WHERE name = ?", (tag,)).fetchone()[
                0
            ]
            self.conn.execute("INSERT INTO itemTags VALUES (?, ?, 0)", (item_id, tag_id))
        for collection in collections:
            self.conn.execute(
                "INSERT INTO collectionItems (collectionID, itemID) VALUES (?, ?)",
                (self._ids[collection], item_id),
            )

    def attachment(
        self,
        key: str,
        *,
        parent: str | None,
        link_mode: int,
        path: str | None,
        content_type: str | None = "application/pdf",
        content: bytes | None = None,
        url: str | None = None,
        client_version: int = 1,
        trashed: bool = False,
    ) -> None:
        """Add an attachment; ``content`` is written to ``storage/KEY/<name>`` for ``storage:``."""
        item_id = self._add(key, "attachment", client_version=client_version, trashed=trashed)
        self.conn.execute(
            """
            INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, None if parent is None else self._ids[parent], link_mode, content_type, path),
        )
        if url is not None:
            self._set_field(item_id, "url", url)
        if content is not None and path is not None and path.startswith("storage:"):
            folder = self.data_dir / "storage" / key
            folder.mkdir(parents=True, exist_ok=True)
            (folder / path.removeprefix("storage:")).write_bytes(content)

    def note(self, key: str, *, parent: str | None, html: str, item_type: str = "note") -> None:
        # item_type "attachment" stores the note on an existing attachment item.
        item_id = self._add(key, "note") if item_type == "note" else self._ids[key]
        self.conn.execute(
            "INSERT INTO itemNotes (itemID, parentItemID, note) VALUES (?, ?, ?)",
            (item_id, None if parent is None else self._ids[parent], html),
        )

    def annotation(
        self,
        key: str,
        *,
        parent: str,
        annotation_type: int = 1,
        text: str | None = None,
        comment: str | None = None,
        color: str | None = "#ffd400",
        page_label: str | None = None,
        trashed: bool = False,
    ) -> None:
        item_id = self._add(key, "annotation", trashed=trashed)
        self.conn.execute(
            """
            INSERT INTO itemAnnotations
                (itemID, parentItemID, type, text, comment, color, pageLabel)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, self._ids[parent], annotation_type, text, comment, color, page_label),
        )

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def sample_library(data_dir: Path) -> ZoteroDb:
    """An English and a Chinese paper, a case, a trashed paper, and their children."""
    db = ZoteroDb.create(data_dir)
    db.collection("CLREAD22", "Reading", client_version=4)
    db.collection("CLCHLD22", "中文文献", parent="CLREAD22", client_version=5)
    db.collection("CLTRSH22", "Old", trashed=True)
    db.collection("CLTRCH22", "Old child", parent="CLTRSH22")
    db.item(
        "ITENGL22",
        fields={
            "title": "Attention study",
            "date": "2020-06-00 June 2020",
            "DOI": "10.1234/attention",
            "url": "https://example.org/attention",
            "abstractNote": "We study attention.",
            "language": "en",
            "extra": "arXiv: 2006.00001\nCitation Key: smith2020\nfree text line",
        },
        creators=[("author", "Jane", "Smith", 0), ("editor", None, "Synthetic Lab", 1)],
        tags=["transformers", "注意力"],
        collections=["CLREAD22", "CLTRSH22"],
        client_version=12,
    )
    db.item(
        "ITCHIN22",
        "book",
        fields={"title": "注意力机制研究", "date": "2021-00-00 ", "language": "zh"},
        creators=[("author", "小明", "王", 0)],
        collections=["CLCHLD22"],
        client_version=14,
    )
    db.item("ITCASE22", "case", fields={"caseName": "Smith v. Jones"}, client_version=3)
    db.item("ITTRSH22", "conferencePaper", fields={"title": "Trashed paper"}, trashed=True)
    db.item("ITGRUP22", fields={"title": "Group paper"}, library_id=2)
    db.attachment(
        "ATENGL22",
        parent="ITENGL22",
        link_mode=1,
        path="storage:attention.pdf",
        content=b"%PDF-1.4 synthetic english",
        url="https://example.org/attention.pdf",
        client_version=13,
    )
    db.attachment(
        "ATCHIN22",
        parent="ITCHIN22",
        link_mode=0,
        path="storage:中文论文.pdf",
        content="%PDF-1.4 合成中文".encode(),
    )
    db.attachment(
        "ATLINK22",
        parent="ITENGL22",
        link_mode=3,
        path=None,
        content_type=None,
        url="https://example.org/page",
    )
    db.attachment("ATMISS22", parent="ITCASE22", link_mode=0, path="storage:missing.pdf")
    db.note("NTCHLD22", parent="ITENGL22", html="<p>My note 我的笔记</p>")
    db.note("NTSTND22", parent=None, html="<p>Standalone</p>")
    db.note("ATENGL22", parent="ITENGL22", html="<p>attachment note</p>", item_type="attachment")
    db.attachment("ATIMGE22", parent="NTCHLD22", link_mode=4, path="storage:image.png")
    db.annotation("ANHIGH22", parent="ATENGL22", text="highlighted", comment="why", page_label="3")
    db.annotation("ANTRSH22", parent="ATENGL22", text="removed", trashed=True)
    db.commit()
    return db
