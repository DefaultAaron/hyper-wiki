"""Find the Zotero data directory and linked-attachment base directory, read-only.

Zotero keeps these settings in its profile (``prefs.js``), not in the data directory:
``extensions.zotero.dataDir`` (used when ``extensions.zotero.useDataDir`` is true) and
``extensions.zotero.baseAttachmentPath``, the "Linked Attachment Base Directory" that linked
files can be stored relative to. ``profiles.ini`` names the profiles. Both files are parsed as
text; nothing is executed, locked, or written.
"""

import configparser
import json
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_USER_PREF = re.compile(r'^\s*user_pref\(\s*("(?:[^"\\]|\\.)*")\s*,\s*(.+?)\s*\)\s*;\s*$')


class ZoteroLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path
    base_attachment_dir: Path | None
    profile_dir: Path | None


def profile_root(*, home: Path, environ: Mapping[str, str], platform: str) -> Path:
    """Zotero's profile root on macOS, Windows, and Linux."""
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Zotero"
    if platform == "win32":
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Zotero" / "Zotero"
    return home / ".zotero" / "zotero"


def read_prefs(path: Path) -> dict[str, object]:
    """Parse ``user_pref("name", value);`` lines whose value is a JSON literal."""
    prefs: dict[str, object] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _USER_PREF.match(line)
        if match is None:
            continue
        try:
            name = json.loads(match.group(1))
            value = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
        if isinstance(name, str):
            prefs[name] = value
    return prefs


def default_profile(root: Path) -> Path | None:
    """The profile marked ``Default=1`` in ``profiles.ini``, or the only one."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(root / "profiles.ini", encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    profiles = [ini[name] for name in ini.sections() if name.startswith("Profile")]
    chosen = [p for p in profiles if p.get("Default") == "1"] or (
        profiles if len(profiles) == 1 else []
    )
    if not chosen or not chosen[0].get("Path"):
        return None
    path = Path(chosen[0]["Path"])
    if chosen[0].get("IsRelative", "1") == "1":
        path = root / path
    return path if (path / "prefs.js").is_file() else None


def _absolute(value: object) -> Path | None:
    if isinstance(value, str) and value and Path(value).is_absolute():
        return Path(value)
    return None


def discover_zotero(
    *,
    data_dir: Path | None = None,
    base_attachment_dir: Path | None = None,
    home: Path,
    environ: Mapping[str, str],
    platform: str,
) -> ZoteroLocation:
    """Locate the data directory and base directory; explicit arguments win.

    The profile's base directory is applied only if that profile uses the data directory being
    read, so another library's setting is never used.
    """
    profile = default_profile(profile_root(home=home, environ=environ, platform=platform))
    prefs: dict[str, object] = {}
    if profile is not None:
        try:
            prefs = read_prefs(profile / "prefs.js")
        except OSError:
            profile = None
    profile_data_dir = home / "Zotero"
    if prefs.get("extensions.zotero.useDataDir") is True:
        profile_data_dir = _absolute(prefs.get("extensions.zotero.dataDir")) or profile_data_dir
    chosen = data_dir if data_dir is not None else profile_data_dir
    base = base_attachment_dir
    if base is None and profile is not None and _same_dir(chosen, profile_data_dir):
        base = _absolute(prefs.get("extensions.zotero.baseAttachmentPath"))
    return ZoteroLocation(data_dir=chosen, base_attachment_dir=base, profile_dir=profile)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.expanduser().resolve()
    except (OSError, RuntimeError):
        return a == b
