"""Zotero-shaped library manifest: the contract every library adapter produces."""

from collections import Counter
from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, NonNegativeInt, model_validator

from hyperwiki.domain.common import Contract, VersionedDocument, ZoteroKey

Availability = Literal["available", "missing", "invalid_path", "remote"]
LinkMode = Literal["imported_file", "imported_url", "linked_file", "linked_url"]
AnnotationType = Literal["highlight", "underline", "note", "image", "ink", "text"]

NonEmptyStr = Annotated[str, Field(min_length=1)]


class LibraryRef(Contract):
    id: NonEmptyStr
    kind: Literal["user", "group"]
    name: NonEmptyStr


class Collection(Contract):
    key: ZoteroKey
    version: NonNegativeInt
    name: NonEmptyStr
    parent_key: ZoteroKey | None


class Creator(Contract):
    creator_type: str
    first_name: str | None = None
    last_name: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _require_name(self) -> Self:
        if not self.name and not self.last_name:
            raise ValueError("a creator needs name or last_name")
        return self


class Item(Contract):
    key: ZoteroKey
    version: NonNegativeInt
    item_type: str
    title: str
    creators: list[Creator]
    date: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    language: str | None
    tags: list[str]
    collection_keys: list[ZoteroKey]
    extra: dict[str, str] = Field(default_factory=dict)
    trashed: bool = False
    date_modified: AwareDatetime


class Attachment(Contract):
    key: ZoteroKey
    version: NonNegativeInt
    parent_key: ZoteroKey | None
    content_type: str
    link_mode: LinkMode
    path: str | None
    url: str | None
    trashed: bool = False
    date_modified: AwareDatetime

    @model_validator(mode="after")
    def _check_link_mode(self) -> Self:
        if self.link_mode == "linked_url":
            if not self.url:
                raise ValueError("a linked_url attachment requires url")
            if self.path is not None:
                raise ValueError("a linked_url attachment must not have a path")
        elif not self.path:
            raise ValueError(f"a {self.link_mode} attachment requires path")
        return self


class Note(Contract):
    key: ZoteroKey
    version: NonNegativeInt
    parent_key: ZoteroKey | None
    html: str
    trashed: bool = False
    date_modified: AwareDatetime


class Annotation(Contract):
    key: ZoteroKey
    version: NonNegativeInt
    parent_key: ZoteroKey
    annotation_type: AnnotationType
    text: str | None
    comment: str | None
    page_label: str | None
    color: str | None
    date_modified: AwareDatetime


def _duplicates(keys: Iterable[str]) -> list[str]:
    return sorted(key for key, count in Counter(keys).items() if count > 1)


class LibraryManifest(VersionedDocument):
    adapter: str
    library: LibraryRef
    snapshot_id: NonEmptyStr
    created_at: AwareDatetime
    completeness: Literal["complete", "partial"]
    storage_root: str = "storage"
    collections: list[Collection]
    items: list[Item]
    attachments: list[Attachment]
    notes: list[Note]
    annotations: list[Annotation]

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        for name, entities in (
            ("collections", self.collections),
            ("items", self.items),
            ("attachments", self.attachments),
            ("notes", self.notes),
            ("annotations", self.annotations),
        ):
            duplicates = _duplicates(entity.key for entity in entities)
            if duplicates:
                raise ValueError(f"duplicate {name} keys: {duplicates}")

        collection_keys = {collection.key for collection in self.collections}
        item_keys = {item.key for item in self.items}
        attachment_keys = {attachment.key for attachment in self.attachments}

        for collection in self.collections:
            if collection.parent_key is not None and collection.parent_key not in collection_keys:
                raise ValueError(
                    f"collection {collection.key} has unknown parent {collection.parent_key}"
                )
        for item in self.items:
            unknown = sorted(set(item.collection_keys) - collection_keys)
            if unknown:
                raise ValueError(f"item {item.key} references unknown collections {unknown}")
        children: list[Attachment | Note] = [*self.attachments, *self.notes]
        for child in children:
            if child.parent_key is not None and child.parent_key not in item_keys:
                raise ValueError(f"{child.key} has unknown parent item {child.parent_key}")
        for annotation in self.annotations:
            if annotation.parent_key not in attachment_keys:
                raise ValueError(
                    f"annotation {annotation.key} has unknown parent attachment "
                    f"{annotation.parent_key}"
                )
        return self
