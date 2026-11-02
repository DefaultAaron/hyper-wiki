"""Read-only adapter for a Zotero-shaped snapshot directory.

A snapshot is ``manifest.json`` plus a storage folder with one subfolder per attachment, as in
Zotero's data directory. Loading never creates, modifies, or deletes anything in the snapshot,
and never opens a file whose path escapes the storage root or the allowed linked-file roots.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path, PurePath

from hyperwiki.domain.common import Contract, Problem, Sha256Hex, ZoteroKey, parse_document
from hyperwiki.domain.library import Attachment, Availability, LibraryManifest
from hyperwiki.errors import ErrorCode, HyperWikiError

MANIFEST_NAME = "manifest.json"
_CHUNK_BYTES = 1024 * 1024
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class AttachmentFile(Contract):
    key: ZoteroKey
    availability: Availability
    resolved_path: Path | None
    sha256: Sha256Hex | None
    size_bytes: int | None


class LoadedSnapshot(Contract):
    directory: Path
    manifest: LibraryManifest
    files: dict[str, AttachmentFile]
    problems: list[Problem]


def _relative_path_error(path: str) -> str | None:
    """Return why ``path`` is not a safe relative POSIX path, or None if it is."""
    if not path:
        return "path is empty"
    if "\\" in path:
        return "path contains a backslash"
    if path.startswith("/"):
        return "path is absolute"
    if _DRIVE_PREFIX.match(path):
        return "path has a drive prefix"
    if ".." in path.split("/"):
        return "path contains a '..' segment"
    if "\x00" in path:
        return "path contains a NUL character"
    return None


def _resolve_inside(path: Path, roots: Sequence[Path]) -> Path | None:
    """Resolve ``path`` (following symlinks) and return it only if it lies inside a root."""
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return None
    if any(resolved.is_relative_to(root) for root in roots):
        return resolved
    return None


def _read_manifest(snapshot_dir: Path) -> object:
    manifest_path = snapshot_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise HyperWikiError(
            ErrorCode.NOT_FOUND,
            f"no {MANIFEST_NAME} in snapshot directory",
            {"path": str(snapshot_dir)},
        )
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HyperWikiError(
            ErrorCode.INVALID_DOCUMENT,
            f"{MANIFEST_NAME} is not valid UTF-8",
            {"path": str(manifest_path), "byte_offset": exc.start},
        ) from exc
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HyperWikiError(
            ErrorCode.INVALID_DOCUMENT,
            f"{MANIFEST_NAME} is not valid JSON: {exc.msg}",
            {"path": str(manifest_path), "line": exc.lineno, "column": exc.colno},
        ) from exc
    return data


def _storage_root(snapshot_dir: Path, storage_root: str) -> Path:
    resolved_dir = snapshot_dir.resolve()
    error = _relative_path_error(storage_root) if storage_root else None
    root = None if error else _resolve_inside(resolved_dir / storage_root, [resolved_dir])
    if root is None:
        raise HyperWikiError(
            ErrorCode.INVALID_PATH,
            error or "storage_root resolves outside the snapshot directory",
            {"storage_root": storage_root},
        )
    return root


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _attachment_file(
    attachment: Attachment,
    storage_root: Path,
    linked_roots: Sequence[Path],
    problems: list[Problem],
) -> AttachmentFile:
    key = attachment.key

    def result(
        availability: Availability,
        resolved: Path | None = None,
        sha256: str | None = None,
        size: int | None = None,
    ) -> AttachmentFile:
        return AttachmentFile(
            key=key,
            availability=availability,
            resolved_path=resolved,
            sha256=sha256,
            size_bytes=size,
        )

    def invalid(message: str) -> AttachmentFile:
        problems.append(
            Problem(code=ErrorCode.INVALID_PATH, entity="attachment", key=key, message=message)
        )
        return result("invalid_path")

    if attachment.link_mode == "linked_url":
        return result("remote")

    path = attachment.path or ""
    if attachment.link_mode == "linked_file":
        if "\x00" in path or not PurePath(path).is_absolute():
            return invalid("linked file path is not an absolute path")
        if not linked_roots:
            return invalid("linked file is outside the allowed linked-file roots (none set)")
        resolved = _resolve_inside(Path(path), linked_roots)
        if resolved is None:
            return invalid("linked file is outside the allowed linked-file roots")
    else:
        error = _relative_path_error(path)
        if error:
            return invalid(error)
        resolved = _resolve_inside(storage_root / path, [storage_root])
        if resolved is None:
            return invalid("path resolves outside the storage root")

    if not resolved.is_file():
        problems.append(
            Problem(
                code=ErrorCode.NOT_FOUND,
                entity="attachment",
                key=key,
                message="attachment file does not exist",
            )
        )
        return result("missing", resolved)
    try:
        sha256, size = _hash_file(resolved)
    except OSError as exc:
        problems.append(
            Problem(
                code=ErrorCode.NOT_FOUND,
                entity="attachment",
                key=key,
                message=f"attachment file cannot be read: {exc.strerror or exc}",
            )
        )
        return result("missing", resolved)
    return result("available", resolved, sha256, size)


def load_snapshot(snapshot_dir: Path, *, linked_file_roots: Sequence[Path] = ()) -> LoadedSnapshot:
    """Read and validate a snapshot, resolving and hashing its attachment files."""
    manifest = parse_document(LibraryManifest, _read_manifest(snapshot_dir))
    storage_root = _storage_root(snapshot_dir, manifest.storage_root)
    linked_roots = [root.resolve() for root in linked_file_roots]
    problems: list[Problem] = []
    files = {
        attachment.key: _attachment_file(attachment, storage_root, linked_roots, problems)
        for attachment in manifest.attachments
    }
    problems.sort(key=lambda problem: (problem.entity, problem.key))
    return LoadedSnapshot(
        directory=snapshot_dir.resolve(),
        manifest=manifest,
        files=files,
        problems=problems,
    )
