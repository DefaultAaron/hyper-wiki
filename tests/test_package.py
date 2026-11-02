from importlib.metadata import metadata, version

import hyperwiki


def test_version_matches_installed_distribution() -> None:
    assert hyperwiki.__version__ == version("hyperwiki")


def test_distribution_declares_apache_license() -> None:
    assert metadata("hyperwiki")["License-Expression"] == "Apache-2.0"
