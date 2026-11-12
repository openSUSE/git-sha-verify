# Copyright SUSE LLC
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _readme_runtime_packages() -> set[str]:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^zypper install (.+)$", readme, flags=re.MULTILINE)
    assert match, "README must document the zypper install line"
    return set(match.group(1).split())


def _containerfile_packages() -> set[str]:
    containerfile = (REPO_ROOT / "container" / "Containerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    match = re.search(r"zypper --non-interactive install --no-recommends ([^&\n]+)", containerfile)
    assert match, "Containerfile must install runtime packages with zypper"
    return set(match.group(1).split())


def test_dockerfile_is_symlink_to_containerfile() -> None:
    dockerfile = REPO_ROOT / "container" / "Dockerfile"
    assert dockerfile.is_symlink()
    assert dockerfile.readlink() == Path("Containerfile")


def test_containerfile_installs_every_runtime_package_from_readme() -> None:
    assert _readme_runtime_packages() <= _containerfile_packages()


def test_containerfile_does_not_bundle_the_checkout_script() -> None:
    containerfile = (REPO_ROOT / "container" / "Containerfile").read_text(encoding="utf-8")
    assert "checkout_last_signed_commit" not in containerfile
