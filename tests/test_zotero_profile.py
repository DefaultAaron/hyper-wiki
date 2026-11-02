import json
from pathlib import Path

import pytest

from hyperwiki.adapters.zotero_profile import ZoteroLocation, discover_zotero, read_prefs


def write_profile(
    root: Path,
    prefs: dict[str, object],
    *,
    name: str = "abcd1234.default",
    extra_profiles: int = 0,
) -> Path:
    """Write a Zotero profile root with ``profiles.ini`` and one default profile's ``prefs.js``."""
    sections = [
        f"[Profile{index}]\nName=other{index}\nIsRelative=1\nPath=Profiles/other{index}\n"
        for index in range(extra_profiles)
    ]
    sections.append(
        f"[Profile{extra_profiles}]\nName=default\nIsRelative=1\nPath=Profiles/{name}\nDefault=1\n"
    )
    sections.append("[General]\nStartWithLastProfile=1\nVersion=2\n")
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles.ini").write_text("\n".join(sections), encoding="utf-8")
    profile = root / "Profiles" / name
    profile.mkdir(parents=True)
    lines = ["// Mozilla User Preferences", ""]
    lines += [f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k, v in prefs.items()]
    (profile / "prefs.js").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return profile


ROOTS = {
    "darwin": lambda home, appdata: home / "Library" / "Application Support" / "Zotero",
    "win32": lambda home, appdata: appdata / "Zotero" / "Zotero",
    "linux": lambda home, appdata: home / ".zotero" / "zotero",
}


@pytest.mark.parametrize("platform", sorted(ROOTS))
def test_discovers_data_dir_from_default_profile(tmp_path: Path, platform: str) -> None:
    home, appdata = tmp_path / "home", tmp_path / "appdata"
    data, base = tmp_path / "custom data", tmp_path / "论文"
    profile = write_profile(
        ROOTS[platform](home, appdata),
        {
            "extensions.zotero.dataDir": str(data),
            "extensions.zotero.useDataDir": True,
            "extensions.zotero.baseAttachmentPath": str(base),
        },
        extra_profiles=1,
    )
    location = discover_zotero(home=home, environ={"APPDATA": str(appdata)}, platform=platform)
    assert location == ZoteroLocation(data_dir=data, base_attachment_dir=base, profile_dir=profile)


def test_defaults_to_home_zotero_without_profile(tmp_path: Path) -> None:
    location = discover_zotero(home=tmp_path, environ={}, platform="darwin")
    assert location == ZoteroLocation(
        data_dir=tmp_path / "Zotero", base_attachment_dir=None, profile_dir=None
    )


def test_data_dir_pref_is_ignored_unless_use_data_dir(tmp_path: Path) -> None:
    write_profile(
        tmp_path / ".zotero" / "zotero",
        {"extensions.zotero.dataDir": "/elsewhere", "extensions.zotero.useDataDir": False},
    )
    location = discover_zotero(home=tmp_path, environ={}, platform="linux")
    assert location.data_dir == tmp_path / "Zotero"


def test_base_dir_only_from_profile_using_this_data_dir(tmp_path: Path) -> None:
    """Another library's base directory must never be applied to the one being read."""
    data, base = tmp_path / "A", tmp_path / "base"
    write_profile(
        tmp_path / ".zotero" / "zotero",
        {
            "extensions.zotero.dataDir": str(data),
            "extensions.zotero.useDataDir": True,
            "extensions.zotero.baseAttachmentPath": str(base),
        },
    )

    def discover(data_dir: Path) -> ZoteroLocation:
        return discover_zotero(data_dir=data_dir, home=tmp_path, environ={}, platform="linux")

    assert discover(tmp_path / "B").base_attachment_dir is None
    assert discover(tmp_path / "B").data_dir == tmp_path / "B"
    assert discover(data).base_attachment_dir == base


def test_explicit_options_override_profile(tmp_path: Path) -> None:
    write_profile(
        tmp_path / ".zotero" / "zotero",
        {"extensions.zotero.baseAttachmentPath": str(tmp_path / "from-profile")},
    )
    location = discover_zotero(
        data_dir=tmp_path / "Zotero",
        base_attachment_dir=tmp_path / "explicit",
        home=tmp_path,
        environ={},
        platform="linux",
    )
    assert location.base_attachment_dir == tmp_path / "explicit"


def test_relative_data_dir_and_empty_base_are_ignored(tmp_path: Path) -> None:
    write_profile(
        tmp_path / ".zotero" / "zotero",
        {
            "extensions.zotero.dataDir": "relative/dir",
            "extensions.zotero.useDataDir": True,
            "extensions.zotero.baseAttachmentPath": "",
        },
    )
    location = discover_zotero(home=tmp_path, environ={}, platform="linux")
    assert location.data_dir == tmp_path / "Zotero"
    assert location.base_attachment_dir is None


def test_prefs_parser_ignores_non_pref_lines(tmp_path: Path) -> None:
    prefs = tmp_path / "prefs.js"
    prefs.write_text(
        "\n".join(
            [
                "// Mozilla User Preferences",
                "/* a block comment */",
                'user_pref("extensions.zotero.dataDir", "/Users/研究者/Zotero");',
                'user_pref("quoted", "say \\"hi\\"");',
                'user_pref("flag", true);',
                'user_pref("count", 23119);',
                'user_pref("broken", undefined_symbol);',
                'pref("not.a.user.pref", 1);',
                "garbage",
            ]
        ),
        encoding="utf-8",
    )
    assert read_prefs(prefs) == {
        "extensions.zotero.dataDir": "/Users/研究者/Zotero",
        "quoted": 'say "hi"',
        "flag": True,
        "count": 23119,
    }


def test_unreadable_profile_falls_back_to_defaults(tmp_path: Path) -> None:
    root = tmp_path / ".zotero" / "zotero"
    root.mkdir(parents=True)
    (root / "profiles.ini").write_text("[Profile0]\nPath=Profiles/missing\n", encoding="utf-8")
    location = discover_zotero(home=tmp_path, environ={}, platform="linux")
    assert location == ZoteroLocation(
        data_dir=tmp_path / "Zotero", base_attachment_dir=None, profile_dir=None
    )
