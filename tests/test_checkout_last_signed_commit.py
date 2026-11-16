# Copyright SUSE LLC
from __future__ import annotations

import runpy
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from requests.exceptions import RequestException

import checkout_last_signed_commit


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def repo_and_mock(repo_dir: Path) -> Iterator[tuple[checkout_last_signed_commit.GitRepo, MagicMock]]:
    with patch("checkout_last_signed_commit.git.Repo") as mock_cls:
        repo = checkout_last_signed_commit.GitRepo(checkout_dir=str(repo_dir), branch="main")
        yield repo, mock_cls.return_value


@pytest.fixture
def mock_main_deps(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[MagicMock, MagicMock, MagicMock]]:
    monkeypatch.setenv("PRIVATE_TOKEN", "fake_token")
    with (
        patch("checkout_last_signed_commit.GitRepo") as mock_repo_cls,
        patch("checkout_last_signed_commit.GitLabUsers") as mock_users_cls,
        patch("checkout_last_signed_commit.GPG") as mock_gpg_cls,
    ):
        yield mock_repo_cls.return_value, mock_users_cls.return_value, mock_gpg_cls.return_value


def test_fatal_error() -> None:
    with pytest.raises(SystemExit) as exc:
        checkout_last_signed_commit.fatal_error("Critical failure")
    assert exc.value.code == 1


def test_gpg_clear_public_keys() -> None:
    gpg = checkout_last_signed_commit.GPG()
    gpg.list_keys = MagicMock(
        side_effect=lambda **kw: (
            [{"fingerprint": "SEC_FP"}]
            if kw.get("secret")
            else [{"fingerprint": "SEC_FP"}, {"fingerprint": "PUB_1"}, {"fingerprint": "PUB_2"}]
        )
    )
    gpg.delete_keys = MagicMock()
    gpg.clear_public_keys()
    assert gpg.delete_keys.call_args_list == [call("PUB_1"), call("PUB_2")]


@pytest.mark.parametrize(("token", "url"), [(None, None), ("val", "https://api.example.com/")])
def test_gitlab_users_init(token: str | None, url: str | None) -> None:
    kwargs = {k: v for k, v in [("private_token", token), ("user_api_url", url)] if v}
    users = checkout_last_signed_commit.GitLabUsers(**kwargs)
    assert users.private_token == token
    assert users.user_api_url == (url or checkout_last_signed_commit.GITLAB_USER_API_DEFAULT)


@pytest.mark.parametrize(
    ("token", "endpoint", "kwargs"),
    [
        ("sample_val", "test/ep", {"timeout": 15}),
        (None, "test/no_token", {}),
    ],
)
@patch("requests.get")
def test_gitlab_users_api_request(
    mock_get: MagicMock,
    token: str | None,
    endpoint: str,
    kwargs: dict[str, int],
) -> None:
    mock_resp = Mock()
    mock_get.return_value = mock_resp
    users = checkout_last_signed_commit.GitLabUsers(private_token=token)
    assert users._api_request(endpoint, **kwargs) == mock_resp  # ruff: ignore[private-member-access]
    mock_resp.raise_for_status.assert_called_once()
    mock_get.assert_called_once_with(
        url=f"https://gitlab.suse.de/api/v4/users/{endpoint}",
        timeout=kwargs.get("timeout", 10),
        headers={"PRIVATE-TOKEN": token} if token else None,
    )


@pytest.mark.parametrize(
    ("user_id", "key_type", "response", "expected"),
    [
        (
            100,
            "ssh",
            [{"key": "ssh-ed25519 AAA"}, {"key": None}, {"key": ""}, {"key": "ssh-rsa BBB"}],
            ["ssh-ed25519 AAA", "ssh-rsa BBB"],
        ),
        (200, "gpg", [{"key": "gpg-key-content"}], ["gpg-key-content"]),
        (300, "ssh", RequestException("Network error"), []),
    ],
)
@patch("checkout_last_signed_commit.GitLabUsers._api_request")
def test_gitlab_users_get_pubkeys(
    mock_api: MagicMock,
    user_id: int,
    key_type: str,
    response: list[dict[str, str | None]] | Exception,
    expected: list[str],
) -> None:
    if isinstance(response, Exception):
        mock_api.side_effect = response
    else:
        mock_api.return_value.json.return_value = response
    assert checkout_last_signed_commit.GitLabUsers().get_pubkeys(user_id, key_type=key_type) == expected
    suffix = f"{user_id}/gpg_keys" if key_type == "gpg" else f"{user_id}/keys"
    mock_api.assert_called_with(suffix)


@pytest.mark.parametrize(
    ("api_resp", "side_effect", "expected"),
    [
        ([{"id": 10}, {"id": 20}, {"id": None}, {}], None, {10, 20}),
        (None, RequestException("Timeout"), set()),
    ],
)
@patch("checkout_last_signed_commit.GitLabUsers._api_request")
def test_gitlab_users_search_user_ids(
    mock_api: MagicMock,
    api_resp: list[dict[str, int | None]] | None,
    side_effect: Exception | None,
    expected: set[int],
) -> None:
    if side_effect:
        mock_api.side_effect = side_effect
    else:
        mock_api.return_value.json.return_value = api_resp
    assert checkout_last_signed_commit.GitLabUsers()._search_user_ids("term") == expected  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    ("email", "side_effects", "expected_ids", "expected_calls"),
    [
        ("alice@suse.com", [{1}], {1}, ["alice@suse.com"]),
        ("john.doe@suse.com", [set(), {2}], {2}, ["john.doe@suse.com", "john"]),
        ("jdoe@suse.de", [set(), set(), {3}], {3}, ["jdoe@suse.de", "jdoe", "doe"]),
        ("nobody@suse.com", [set(), set(), set()], set(), ["nobody@suse.com", "nobody", "obody"]),
    ],
)
@patch("checkout_last_signed_commit.GitLabUsers._search_user_ids")
def test_gitlab_users_get_user_ids(
    mock_search: MagicMock,
    email: str,
    side_effects: list[set[int]],
    expected_ids: set[int],
    expected_calls: list[str],
) -> None:
    mock_search.side_effect = side_effects
    assert checkout_last_signed_commit.GitLabUsers().get_user_ids(email) == expected_ids
    assert mock_search.call_args_list == [call(c) for c in expected_calls]


@pytest.mark.parametrize("url", ["https://example.com/repo.git", None])
@patch("checkout_last_signed_commit.git.Repo")
def test_git_repo_init_new_repo(
    mock_git_repo_cls: MagicMock,
    tmp_path: Path,
    url: str | None,
) -> None:
    mock_instance = MagicMock()
    mock_git_repo_cls.init.side_effect = lambda p, **_: (Path(p) / ".git").mkdir(parents=True) or mock_instance
    target_dir = tmp_path / "test_repo"
    if url is None:
        with pytest.raises(SystemExit) as exc:
            checkout_last_signed_commit.GitRepo(checkout_dir=str(target_dir), repo_url=url)
        assert exc.value.code == 1
    else:
        repo = checkout_last_signed_commit.GitRepo(checkout_dir=str(target_dir), repo_url=url, branch="feature")
        assert repo.repo_url == url
        assert repo.ref == "origin/feature"
        mock_git_repo_cls.init.assert_called_once_with(target_dir, initial_branch="main")
        mock_instance.create_remote.assert_called_once_with("origin", url=url, tags=False)
        mock_instance.config_writer.return_value.set_value.assert_called_once_with(
            'gpg "ssh"', "allowedSignersFile", str(target_dir / ".git" / "allowed_signers_file")
        )


@pytest.mark.parametrize("given_url", ["https://example.com/repo.git", None, "https://different.com/repo.git"])
@patch("checkout_last_signed_commit.git.Repo")
def test_git_repo_init_existing_repo(mock_git_repo_cls: MagicMock, repo_dir: Path, given_url: str | None) -> None:
    mock_git_repo_cls.return_value.remote.return_value.url = "https://example.com/repo.git"
    if given_url == "https://different.com/repo.git":
        with pytest.raises(SystemExit) as exc:
            checkout_last_signed_commit.GitRepo(checkout_dir=str(repo_dir), repo_url=given_url, branch="main")
        assert exc.value.code == 1
    else:
        repo = checkout_last_signed_commit.GitRepo(checkout_dir=str(repo_dir), repo_url=given_url, branch="main")
        assert repo.repo == mock_git_repo_cls.return_value


def test_git_repo_clear_allowed_signers_file(repo_dir: Path) -> None:
    signers = repo_dir / ".git" / "allowed_signers_file"
    signers.write_text("existing signer\n", encoding="utf-8")
    with patch("checkout_last_signed_commit.git.Repo"):
        repo = checkout_last_signed_commit.GitRepo(checkout_dir=str(repo_dir), branch="main")
    assert signers.read_text(encoding="utf-8") == "existing signer\n"
    repo.clear_allowed_signers_file()
    assert signers.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("remote_out", "expected"),
    [
        ("  HEAD branch: production\n  Remote branch: origin/main", "production"),
        ("No HEAD branch info", None),
    ],
)
@patch("checkout_last_signed_commit.git.Repo")
def test_git_repo_get_remote_default_branch(
    mock_git_repo_cls: MagicMock, repo_dir: Path, remote_out: str, expected: str | None
) -> None:
    mock_git_repo_cls.return_value.git.remote.return_value = remote_out
    if expected is None:
        repo = checkout_last_signed_commit.GitRepo(checkout_dir=str(repo_dir), branch="main")
        with pytest.raises(SystemExit) as exc:
            repo._get_remote_default_branch()  # ruff: ignore[private-member-access]
        assert exc.value.code == 1
    else:
        repo = checkout_last_signed_commit.GitRepo(checkout_dir=str(repo_dir), branch=None)
        assert repo.ref == f"origin/{expected}"
        assert repo._get_remote_default_branch() == expected  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    ("output_line", "cpu_count", "depth"),
    [
        ("remote: Counting objects: 10, done.\n", 4, 5),
        (
            "remote: Total 0 (delta 0), reused 0 (delta 0)\n",
            None,
            checkout_last_signed_commit.GIT_FETCH_DEPTH_LIMIT + 10,
        ),
    ],
)
def test_git_repo_fetch(
    repo_and_mock: tuple[checkout_last_signed_commit.GitRepo, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    output_line: str,
    cpu_count: int | None,
    depth: int,
) -> None:
    repo, mock_repo = repo_and_mock
    mock_repo.git.fetch.return_value = ("0", "", output_line)
    monkeypatch.setattr("os.cpu_count", lambda: cpu_count)
    has_new = "Total 0" not in output_line
    assert repo.fetch(depth=depth) is has_new
    mock_repo.git.fetch.assert_called_with(
        "origin",
        "--no-tags",
        "--no-show-forced-updates",
        with_extended_output=True,
        progress=True,
        jobs=max(1, (cpu_count or 1) - 1),
        depth=min(checkout_last_signed_commit.GIT_FETCH_DEPTH_LIMIT, depth),
    )


def test_git_repo_get_committer_email_addrs(
    repo_and_mock: tuple[checkout_last_signed_commit.GitRepo, MagicMock],
) -> None:
    repo, mock_repo = repo_and_mock
    commits = [
        MagicMock(committer=MagicMock(email=e)) for e in ["alice@suse.com", "bob@suse.com", "alice@suse.com", "", None]
    ]
    mock_repo.iter_commits.return_value = commits
    assert repo.get_committer_email_addrs() == {"alice@suse.com", "bob@suse.com"}
    mock_repo.iter_commits.assert_called_once_with("origin/main", committer="suse")


@pytest.mark.parametrize(
    ("log_output", "key_types", "expected_sha"),
    [
        ('"sha_ssh G SHA256:fingerprint123"\n"sha_other G 4AEE18F8"', {"ssh"}, "sha_ssh"),
        ('"sha_bad B SHA256:bad"\n"sha_good G SHA256:good"', {"ssh"}, "sha_good"),
        ('"sha_gpg_g G 4AEE18F8"', {"gpg"}, "sha_gpg_g"),
        ('"sha_gpg_u U 4AEE18F8"', {"gpg"}, "sha_gpg_u"),
        ('"sha1 B 4AEE18F8"\n"sha2 N none"', {"gpg"}, None),
        ('"sha_gpg G 4AEE18F8"', {"ssh"}, None),
    ],
)
def test_git_repo_checkout_latest_signed_commit(
    repo_and_mock: tuple[checkout_last_signed_commit.GitRepo, MagicMock],
    log_output: str,
    key_types: set[str],
    expected_sha: str | None,
) -> None:
    repo, mock_repo = repo_and_mock
    mock_repo.git.log.return_value = log_output
    assert repo.checkout_latest_signed_commit(key_types=key_types) == expected_sha
    if expected_sha:
        mock_repo.git.checkout.assert_called_once_with(expected_sha)
    else:
        mock_repo.git.checkout.assert_not_called()


def test_git_repo_import_ssh_keys(
    repo_and_mock: tuple[checkout_last_signed_commit.GitRepo, MagicMock],
) -> None:
    repo, _ = repo_and_mock
    signers_file = repo.allowed_signers_file
    signers_file.write_text('existing@suse.com namespaces="git" ssh-ed25519 KEY0\n', encoding="utf-8")

    with patch("checkout_last_signed_commit.logger.info") as mock_info:
        repo.import_ssh_keys("user@suse.com", ["ssh-dss DEPRECATED_KEY", "ssh-ed25519 KEY1"])
    mock_info.assert_called_with("Ignoring deprecated ssh-dss key for %s", "user@suse.com")

    content = signers_file.read_text(encoding="utf-8")
    assert 'existing@suse.com namespaces="git" ssh-ed25519 KEY0\n' in content
    assert 'user@suse.com namespaces="git" ssh-ed25519 KEY1\n' in content

    # Re-importing existing key does not duplicate
    repo.import_ssh_keys("user@suse.com", ["ssh-ed25519 KEY1"])
    assert signers_file.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["-t", "d"], ("d", None, None, ["ssh", "gpg"], False, False, False, False)),
        (
            ["-t", "d", "-u", "https://x/r.git", "-b", "f", "-k", "ssh", "gpg", "-c", "-p", "-n", "-v"],
            ("d", "https://x/r.git", "f", ["ssh", "gpg"], True, True, True, True),
        ),
    ],
)
def test_parse_args(argv: list[str], expected: tuple[object, ...]) -> None:
    attrs = (
        "target_dir",
        "url",
        "branch",
        "key_types",
        "clean_gpg_keystore",
        "persist_ssh_keystore",
        "no_key_sync",
        "verbose",
    )
    args = checkout_last_signed_commit.parse_args(argv)
    assert tuple(getattr(args, a) for a in attrs) == expected


def test_parse_args_invalid() -> None:
    with pytest.raises(SystemExit):
        checkout_last_signed_commit.parse_args(["-t", "target_dir", "-k", "invalid_type"])


def test_main_verbose_and_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRIVATE_TOKEN", raising=False)
    with (
        patch("checkout_last_signed_commit.GitRepo"),
        patch("checkout_last_signed_commit.logger.setLevel") as mock_set_level,
        pytest.raises(SystemExit) as exc,
    ):
        checkout_last_signed_commit.Main(["-t", "dir", "-v"]).run()
    assert exc.value.code == 1
    mock_set_level.assert_called_with(checkout_last_signed_commit.logging.DEBUG)


@pytest.mark.parametrize(
    "argv",
    [["-t", "dir", "-k", "gpg", "-c"], ["-t", "dir"]],
)
def test_main_no_new_commits_valid_head(
    mock_main_deps: tuple[MagicMock, MagicMock, MagicMock],
    argv: list[str],
) -> None:
    mock_repo, _, mock_gpg = mock_main_deps
    mock_repo.fetch.return_value = False
    mock_repo.repo.head.is_valid.return_value = True

    checkout_last_signed_commit.Main(argv).run()

    mock_repo.fetch.assert_called_once_with(checkout_last_signed_commit.INITIAL_GIT_FETCH_DEPTH)
    assert mock_gpg.clear_public_keys.called == ("-c" in argv)


@pytest.mark.parametrize(
    ("argv", "fetch_side_effect", "config"),
    [
        (["-t", "dir", "-k", "gpg", "-c", "-n"], [], {}),
        (["-t", "dir"], [False, False], {"head_valid": False}),
        (["-t", "dir"], [True, False], {}),
        (["-t", "dir", "-k", "gpg"], [True, False], {"emails": {"bob@suse.com"}, "users": {202}, "pubkeys": ["bad"]}),
        (["-t", "dir", "-k", "gpg", "ssh"], [True, False], {"emails": {"dan@suse.com"}, "users": {404}}),
    ],
)
def test_main_fetch_or_import_failures(
    mock_main_deps: tuple[MagicMock, MagicMock, MagicMock],
    argv: list[str],
    fetch_side_effect: list[bool],
    config: dict[str, object],
) -> None:
    mock_repo, mock_users, mock_gpg = mock_main_deps
    mock_repo.fetch.side_effect = fetch_side_effect
    mock_repo.repo.head.is_valid.return_value = config.get("head_valid", True)
    mock_repo.get_committer_email_addrs.return_value = config.get("emails", set())
    mock_users.get_user_ids.return_value = config.get("users", set())
    mock_users.get_pubkeys.return_value = config.get("pubkeys", [])
    mock_gpg.import_keys.return_value.count = config.get("gpg_count", 0)

    with pytest.raises(SystemExit) as exc:
        checkout_last_signed_commit.Main(argv).run()
    assert exc.value.code == 1
    mock_repo.checkout_latest_signed_commit.assert_not_called()


@pytest.mark.parametrize(
    ("argv", "exp_checkout_keys"),
    [
        (["-t", "dir", "-k", "gpg", "ssh"], ["gpg", "ssh"]),
        (["-t", "dir"], ["ssh", "gpg"]),
        (["-t", "dir", "-n"], ["ssh", "gpg"]),
    ],
)
def test_main_sync_and_checkout(
    mock_main_deps: tuple[MagicMock, MagicMock, MagicMock],
    argv: list[str],
    exp_checkout_keys: list[str],
) -> None:
    mock_repo, mock_users, mock_gpg = mock_main_deps
    mock_repo.fetch.return_value = True
    mock_repo.get_committer_email_addrs.return_value = {"user@suse.com"}
    mock_repo.checkout_latest_signed_commit.return_value = "commit_sha"
    mock_users.get_user_ids.return_value = {101}
    mock_users.get_pubkeys.return_value = ["mock_key"]
    mock_gpg.import_keys.return_value.count = 1

    checkout_last_signed_commit.Main(argv).run()

    if "-n" not in argv:
        mock_repo.import_ssh_keys.assert_called_once_with("user@suse.com", ["mock_key"])
    else:
        mock_repo.get_committer_email_addrs.assert_not_called()
    mock_repo.checkout_latest_signed_commit.assert_called_once_with(exp_checkout_keys)


def test_main_retry_fetch_with_increased_depth(mock_main_deps: tuple[MagicMock, MagicMock, MagicMock]) -> None:
    mock_repo, _, _ = mock_main_deps
    mock_repo.fetch.return_value = True
    mock_repo.checkout_latest_signed_commit.side_effect = [None, "retry_sha"]
    checkout_last_signed_commit.Main(["-t", "dir", "-n"]).run()
    assert mock_repo.fetch.call_args_list == [
        call(checkout_last_signed_commit.INITIAL_GIT_FETCH_DEPTH),
        call(checkout_last_signed_commit.INITIAL_GIT_FETCH_DEPTH * 2),
    ]


def test_main_depth_limit_reached(
    mock_main_deps: tuple[MagicMock, MagicMock, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_repo, _, _ = mock_main_deps
    mock_repo.fetch.return_value = True
    mock_repo.checkout_latest_signed_commit.return_value = None
    monkeypatch.setattr(checkout_last_signed_commit, "GIT_FETCH_DEPTH_LIMIT", 4)
    with pytest.raises(SystemExit) as exc:
        checkout_last_signed_commit.Main(["-t", "dir", "-n"]).run()
    assert exc.value.code == 1


def test_main_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_repo = MagicMock()
    mock_repo.head.is_valid.return_value = True
    mock_repo.git.fetch.return_value = ("0", "", "remote: Total 0 (delta 0), reused 0 (delta 0)")
    monkeypatch.setattr("git.Repo", MagicMock(return_value=mock_repo))

    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(sys, "argv", ["checkout_last_signed_commit.py", "-t", str(tmp_path), "-b", "main", "-n"])

    runpy.run_path("checkout_last_signed_commit.py", run_name="__main__")
