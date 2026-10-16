# Copyright SUSE LLC
"""Tests for the log output of the checkout-latest-signed-commit bash script.

These tests use local file:// repositories only, so they need no network access.
"""

from __future__ import annotations

import os
import subprocess  # ruff: ignore[suspicious-subprocess-import]
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "checkout-latest-signed-commit"


@pytest.fixture
def git_env(tmp_path: Path) -> dict[str, str]:
    """Return an environment isolated from the user's git and gpg configuration."""
    env = {k: v for k, v in os.environ.items() if k != "VERBOSE"}
    env.update({
        "HOME": str(tmp_path / "home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    })
    (tmp_path / "home").mkdir()
    return env


@pytest.fixture
def remote_url(tmp_path: Path, git_env: dict[str, str]) -> str:
    """Create a bare repository containing one unsigned commit and return its file:// URL."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    for cmd in (
        ["git", "init", "-q", "--bare", "-b", "master", str(bare)],
        ["git", "init", "-q", "-b", "master", str(work)],
        ["git", "-C", str(work), "commit", "-q", "--allow-empty", "-m", "unsigned"],
        ["git", "-C", str(work), "push", "-q", str(bare), "master"],
    ):
        subprocess.run(cmd, check=True, env=git_env, capture_output=True)  # ruff: ignore[subprocess-without-shell-equals-true]
    return f"file://{bare}"


def run_script(
    env: dict[str, str], target: Path, url: str | None, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    args = ["bash", str(SCRIPT), str(target)]
    if url is not None:
        args.append(url)
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        args, env={**env, **extra_env}, capture_output=True, text=True, check=False, timeout=60
    )


def test_logs_progress_and_final_error_when_no_signed_commit(
    tmp_path: Path, git_env: dict[str, str], remote_url: str
) -> None:
    target = tmp_path / "checkout"
    result = run_script(git_env, target, remote_url)

    assert result.returncode == 1
    for expected in (
        "Initializing repo",
        "Fetching with depth 2",
        "Fetching with depth 4",
    ):
        assert expected in result.stdout
    assert "Couldn't find a signed commit" in result.stderr


def test_logs_nothing_new_on_existing_repo(tmp_path: Path, git_env: dict[str, str], remote_url: str) -> None:
    target = tmp_path / "checkout"
    run_script(git_env, target, remote_url)

    result = run_script(git_env, target, remote_url)

    assert result.returncode == 0
    assert "Initializing repo" not in result.stdout
    assert "Fetching with depth 2" in result.stdout
    assert "Nothing new on the server" in result.stdout


def test_logs_remote_mismatch(tmp_path: Path, git_env: dict[str, str], remote_url: str) -> None:
    target = tmp_path / "checkout"
    run_script(git_env, target, remote_url)
    other_url = "file:///nonexistent/other.git"

    result = run_script(git_env, target, other_url)

    assert result.returncode == 1
    assert f"Repo already present with different remote url: {remote_url}" in result.stderr


def test_git_fetch_failure_exits_with_git_status(tmp_path: Path, git_env: dict[str, str]) -> None:
    result = run_script(git_env, tmp_path / "checkout", "file:///nonexistent/missing.git")

    assert result.returncode == 128
    assert "Fetching with depth 2" in result.stdout


def test_logs_error_when_no_checkout_and_no_url(tmp_path: Path, git_env: dict[str, str]) -> None:
    target = tmp_path / "checkout"
    result = run_script(git_env, target, None)

    assert result.returncode == 1
    assert f"No previous git checkout at {target} and no URL provided" in result.stderr
