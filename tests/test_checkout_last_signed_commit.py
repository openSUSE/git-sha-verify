# Copyright SUSE LLC
import pathlib
import unittest
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest
from requests import RequestException
from requests.exceptions import HTTPError

import checkout_last_signed_commit


class Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.monkey_patch = pytest.MonkeyPatch()

    def test_git_lab_gpg_key_fetcher_uninitialized(self) -> None:
        self.monkey_patch.delenv(name="PRIVATE_TOKEN", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            gpg_key_fetcher = checkout_last_signed_commit.GitLabGPGKeyFetcher()
        assert exc_info.type is SystemExit
        assert (
            exc_info.value.code
            == "Please set the environment variable PRIVATE_TOKEN for GitLab User API Authentication"
        )

        self.monkey_patch.setenv(name="PRIVATE_TOKEN", value="my_temporary_value")
        gpg_key_fetcher = checkout_last_signed_commit.GitLabGPGKeyFetcher()
        assert gpg_key_fetcher.user_api_url == checkout_last_signed_commit.GITLAB_USER_API_DEFAULT
        assert gpg_key_fetcher.user_email is None
        assert gpg_key_fetcher.get_gpg_key_by_uid() is None
        assert gpg_key_fetcher.fetch_user_uid() == []

    def test_checkout_verified_commit_uninitialized(self) -> None:
        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit()
        assert commit_checker.path_to_checkout_dir is None
        assert commit_checker.repository_url is None
        assert commit_checker.commit_sha is None
        assert commit_checker.uid is None
        assert commit_checker.repo_instance is None
        assert commit_checker.create_checkout_dir() == commit_checker.path_to_checkout_dir

        with pytest.raises(SystemExit) as exc_info:
            commit_checker.init_or_load_repo()
        assert exc_info.type is SystemExit
        assert (
            exc_info.value.code
            == f"No previous git checkout at {commit_checker.path_to_checkout_dir} and no URL provided"
        )
        assert commit_checker.get_default_remote_branch() is None
        assert commit_checker.get_commiter_email() == []
        assert commit_checker.get_signed_commit_sha() is None

    @patch("requests.get")
    def test_git_lab_gpg_key_fetcher_initialized(self, mock_get: MagicMock) -> None:
        self.monkey_patch.setenv("PRIVATE_TOKEN", "my_temporary_value")

        """Test fetch_user_uid by **Name** (failure), call fetch_user_uid() with none as an argument"""
        mock_data = []
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = mock_data
        mock_get.return_value = mock_response

        gpg_key_fetcher = checkout_last_signed_commit.GitLabGPGKeyFetcher(
            user_email="fake.user@email.com", user_api_url="http://api.example.com/users/"
        )

        uid = gpg_key_fetcher.fetch_user_uid(email=None)
        mock_get.assert_called_with(
            url="http://api.example.com/users/?search=fake", headers={"PRIVATE-TOKEN": "my_temporary_value"}, timeout=10
        )
        assert uid == []

        """
        Test fetch_user_uid by **Email** (success), instantiate GitLabGPGKeyFetcher
        with mock userid and user API URL
        """
        mock_data = [{"id": 100, "username": "mockuser"}, {"id": 200, "username": "usermock"}]
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_data
        mock_get.return_value = mock_response

        uid = gpg_key_fetcher.fetch_user_uid(email="mock.user@fake.com")
        mock_get.assert_called_with(
            url="http://api.example.com/users/?search=mock.user@fake.com",
            headers={"PRIVATE-TOKEN": "my_temporary_value"},
            timeout=10,
        )
        assert uid == [100, 200]

        """Test _search_user_ids by **username**"""
        mock_data = [{"id": 300, "username": "mockuser"}, {"id": 400, "username": "usermock"}]
        mock_response.json.return_value = mock_data
        uid = gpg_key_fetcher._search_user_ids("mock.user")  # noqa: SLF001
        mock_get.assert_called_with(
            url="http://api.example.com/users/?search=mock.user",
            headers={"PRIVATE-TOKEN": "my_temporary_value"},
            timeout=10,
        )
        assert uid == [300, 400]

        """Test _search_user_ids by **Name**"""
        mock_data = [{"id": 500, "username": "Fake User"}, {"id": 600, "username": "johndoe"}]
        mock_response.json.return_value = mock_data
        uid = gpg_key_fetcher._fetch_user_uid_by_name("Fake User")  # noqa: SLF001
        mock_get.assert_called_with(
            url="http://api.example.com/users/?search=Fake User",
            headers={"PRIVATE-TOKEN": "my_temporary_value"},
            timeout=10,
        )
        assert uid == [500, 600]

        """Test _search_user_ids by firstname.lastname extracted from email part"""
        mock_data = [{"id": 700, "username": "Fake User"}, {"id": 800, "username": "johndoe"}]
        mock_response.json.return_value = mock_data
        uid = gpg_key_fetcher._fetch_user_uid_by_name(name=None)  # noqa: SLF001
        mock_get.assert_called_with(
            url="http://api.example.com/users/?search=fake.user",
            headers={"PRIVATE-TOKEN": "my_temporary_value"},
            timeout=10,
        )
        assert uid == [700, 800]

        """Test _search_user_ids for empty response with error status as HTTP response"""
        mock_response.status_code = 500
        mock_response.raise_for_status = Mock()
        mock_response.raise_for_status.side_effect = HTTPError("gitlab is down")
        uid = gpg_key_fetcher._fetch_user_uid_by_name("Fake User")  # noqa: SLF001
        mock_get.assert_called_with(
            url="http://api.example.com/users/?search=Fake User",
            headers={"PRIVATE-TOKEN": "my_temporary_value"},
            timeout=10,
        )
        assert uid == []

        """Test _search_user_ids for empty return value"""
        gpg_key_fetcher.user_email = None
        uid = gpg_key_fetcher._fetch_user_uid_by_name(name=None)  # noqa: SLF001
        assert uid == []

        """Test get_gpg_key_by_uid return value for mocked GPG Key"""
        mock_gpg_key = "THE-FAKE-AND-MOCKED-GPG-KEY"
        mock_data = [{"key": mock_gpg_key}]
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_data
        mock_get.return_value = mock_response

        gpg_key = gpg_key_fetcher.get_gpg_key_by_uid(uid=500)
        mock_get.assert_called_with(
            url="http://api.example.com/users/500/gpg_keys", headers={"PRIVATE-TOKEN": "my_temporary_value"}
        )
        assert gpg_key == mock_gpg_key

        """Test get_gpg_key_by_uid for empty response list with 200 status code"""
        mock_response.status_code = 200
        mock_response.json.return_value = []
        gpg_key = gpg_key_fetcher.get_gpg_key_by_uid(uid=500)
        assert gpg_key is None

        """Test get_gpg_key_by_uid for empty response with error status as HTTP response"""
        mock_response.status_code = 404
        mock_response.raise_for_status = Mock()
        mock_response.raise_for_status.side_effect = HTTPError("GPG Key Not Found!")
        gpg_key = gpg_key_fetcher.get_gpg_key_by_uid(uid=500)
        mock_get.assert_called_with(
            url="http://api.example.com/users/500/gpg_keys", headers={"PRIVATE-TOKEN": "my_temporary_value"}
        )
        assert gpg_key is None

        """Test get_gpg_key_by_uid for empty response with Request Exception"""
        mock_response.status_code = 404
        mock_response.raise_for_status = Mock()
        mock_response.raise_for_status.side_effect = RequestException("Mocked Request Exception")
        gpg_key = gpg_key_fetcher.get_gpg_key_by_uid(uid=500)
        mock_get.assert_called_with(
            url="http://api.example.com/users/500/gpg_keys", headers={"PRIVATE-TOKEN": "my_temporary_value"}
        )
        assert gpg_key is None

    @patch("checkout_last_signed_commit.Path")
    def test_create_checkout_dir(self, mock_path: MagicMock) -> None:
        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit(target_dir="fake_dir")

        # Success case
        assert commit_checker.create_checkout_dir() == "fake_dir"
        mock_path.return_value.mkdir.assert_called_once_with(mode=0o766, parents=True, exist_ok=True)

        # Exception case
        mock_path.return_value.mkdir.side_effect = Exception("mkdir error")
        res_err = commit_checker.create_checkout_dir()
        assert isinstance(res_err, Exception)
        assert str(res_err) == "mkdir error"

    @patch("checkout_last_signed_commit.git.Repo")
    @patch("checkout_last_signed_commit.Path")
    def test_init_or_load_repo(self, mock_path: MagicMock, mock_repo_class: MagicMock) -> None:
        # Case 1: .git does not exist, URL is provided -> Repo.init
        mock_git_path = MagicMock()
        mock_git_path.exists.return_value = False
        mock_git_path.is_dir.return_value = False
        mock_path.return_value = mock_git_path

        mock_repo = MagicMock()
        mock_repo_class.init.return_value = mock_repo

        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit(
            target_dir="fake_dir", repo_url="https://github.com/example/repo.git"
        )
        commit_checker.init_or_load_repo()

        mock_repo_class.init.assert_called_once_with("fake_dir", initial_branch="main")
        mock_repo.create_remote.assert_called_once_with("origin", url="https://github.com/example/repo.git", tags=False)
        mock_repo.config_writer.return_value.set_value.assert_called_once_with(
            'gpg "ssh"', "allowedSignersFile", "/dev/null"
        )
        mock_repo.config_writer.return_value.set_value.return_value.release.assert_called_once()

        # Case 2: .git exists -> git.Repo(path)
        mock_repo_class.init.reset_mock()
        mock_path.side_effect = None

        mock_git_path = MagicMock()
        mock_git_path.exists.return_value = True
        mock_git_path.is_dir.return_value = True
        mock_path.return_value = mock_git_path

        commit_checker2 = checkout_last_signed_commit.GitCheckVerifiedCommit(
            target_dir="fake_dir", repo_url="https://github.com/example/repo.git"
        )
        commit_checker2.init_or_load_repo()
        mock_repo_class.assert_called_with("fake_dir/.git")

    def test_fetch_git_repo(self) -> None:
        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit(target_dir="fake_dir")

        # repo_instance is None
        assert commit_checker.fetch_git_repo() is None

        # repo_instance is not None
        mock_repo = MagicMock()
        mock_repo.git.fetch.return_value = ("status", "stdout", "stderr_output")
        commit_checker.repo_instance = mock_repo

        res = commit_checker.fetch_git_repo(depth_val=5)
        assert res == "stderr_output"
        mock_repo.git.fetch.assert_called_once_with(
            "origin",
            "--no-tags",
            "--no-show-forced-updates",
            with_extended_output=True,
            progress=True,
            jobs=ANY,
            depth=5,
        )

    def test_get_default_remote_branch(self) -> None:
        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit(target_dir="fake_dir")

        # Case when repo_instance is None
        assert commit_checker.get_default_remote_branch() is None

        # Parameterized cases when repo_instance is not None
        test_cases = [
            ("  HEAD branch: main\n  Some other info", "main"),
            ("No HEAD branch info", None),
        ]
        for remote_output, expected_branch in test_cases:
            mock_repo = MagicMock()
            mock_repo.git.remote.return_value = remote_output
            commit_checker.repo_instance = mock_repo
            assert commit_checker.get_default_remote_branch() == expected_branch
            mock_repo.git.remote.assert_called_once_with("show", "origin")

    @patch.object(checkout_last_signed_commit.GitCheckVerifiedCommit, "get_default_remote_branch")
    def test_get_commiter_email(self, mock_get_def_branch: MagicMock) -> None:
        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit(target_dir="fake_dir")

        # Case 1: branch is not None, repo_instance is None
        assert commit_checker.get_commiter_email(git_branch="dev") == []

        # Case 2: branch is None, repo_instance is not None
        mock_get_def_branch.return_value = "main"
        mock_repo = MagicMock()
        mock_commit1 = MagicMock()
        mock_commit1.committer.email = "user1@suse.com"
        mock_commit2 = MagicMock()
        mock_commit2.committer.email = "user2@suse.com"
        mock_commit3 = MagicMock()
        mock_commit3.committer.email = "user1@suse.com"
        mock_repo.iter_commits.return_value = [mock_commit1, mock_commit2, mock_commit3]
        commit_checker.repo_instance = mock_repo

        emails = commit_checker.get_commiter_email(git_branch=None)
        assert emails == ["user1@suse.com", "user2@suse.com"]
        mock_get_def_branch.assert_called_once()
        mock_repo.iter_commits.assert_called_once_with("origin/main", committer="suse")

    def test_get_signed_commit_sha(self) -> None:
        commit_checker = checkout_last_signed_commit.GitCheckVerifiedCommit(target_dir="fake_dir")

        # Case when repo_instance is None
        assert commit_checker.get_signed_commit_sha("main") is None

        # Parameterized cases when repo_instance is not None
        test_cases = [
            ("main", "G abcdef1234567890\nN baddecaf", "abcdef1234567890"),
            ("dev", "B somehash\nU fedcba0987654321", "fedcba0987654321"),
            ("dev", "B somehash\nN otherhash", None),
        ]
        for branch, git_log, expected_sha in test_cases:
            mock_repo = MagicMock()
            mock_repo.git.log.return_value = git_log
            commit_checker.repo_instance = mock_repo
            assert commit_checker.get_signed_commit_sha(branch) == expected_sha
            mock_repo.git.log.assert_called_once_with(f"origin/{branch}", '--pretty="%G? %H"')

    def test_parse_args(self) -> None:
        args = checkout_last_signed_commit.parse_args(["-t", "fake_dir", "-u", "https://example.com/repo.git"])
        assert args.target_dir == "fake_dir"
        assert args.url == "https://example.com/repo.git"

    @patch("checkout_last_signed_commit.GitCheckVerifiedCommit")
    @patch("checkout_last_signed_commit.GitLabGPGKeyFetcher")
    @patch("checkout_last_signed_commit.gnupg.GPG")
    def test_main_scenarios(
        self,
        mock_gpg_class: MagicMock,
        mock_fetcher_class: MagicMock,
        mock_checker_class: MagicMock,
    ) -> None:
        self.monkey_patch.setenv("PRIVATE_TOKEN", "fake_token")

        scenarios = [
            # Case 1: No new commits found
            {
                "fetch_repo_ret": "remote: Total 0 ",
                "committer_emails": [],
                "uids": [],
                "gpg_key_side": None,
                "import_code": 0,
                "signed_commit": None,
                "expected_exit": "No new commits found on server",
                "expected_checkout": None,
            },
            # Case 2: Success with duplicate emails
            {
                "fetch_repo_ret": "new commits",
                "committer_emails": ["skipped@suse.com", "skipped@suse.com", "dev@suse.com"],
                "uids": [[123], [456]],
                "gpg_key_side": "gpg-key-content",
                "import_code": 0,
                "signed_commit": [None, "abcdef123456"],
                "expected_exit": None,
                "expected_checkout": "abcdef123456",
            },
            # Case 3: Import failure and retry with second key/uid
            {
                "fetch_repo_ret": "new commits",
                "committer_emails": ["developer@suse.com"],
                "uids": [123, 456],
                "gpg_key_side": [None, "good-gpg-key"],
                "import_code": 1,
                "signed_commit": "abcdef123456",
                "expected_exit": None,
                "expected_checkout": "abcdef123456",
            },
            # Case 4: Success with empty emails on first try, exits on second try
            {
                "fetch_repo_ret": "new commits",
                "committer_emails": [[], ["dev@suse.com"]],
                "uids": [789],
                "gpg_key_side": "gpg-key-content-3",
                "import_code": 0,
                "signed_commit": "abcdef987654",
                "expected_exit": None,
                "expected_checkout": "abcdef987654",
            },
        ]

        for s in scenarios:
            mock_gpg_class.reset_mock()
            mock_fetcher_class.reset_mock()
            mock_checker_class.reset_mock()

            mock_checker = MagicMock()
            mock_checker.fetch_git_repo.return_value = s["fetch_repo_ret"]

            if s["committer_emails"] and isinstance(s["committer_emails"][0], list):
                mock_checker.get_commiter_email.side_effect = s["committer_emails"]
            else:
                mock_checker.get_commiter_email.return_value = s["committer_emails"]

            if isinstance(s["signed_commit"], list):
                mock_checker.get_signed_commit_sha.side_effect = s["signed_commit"]
            else:
                mock_checker.get_signed_commit_sha.return_value = s["signed_commit"]

            mock_checker_class.return_value = mock_checker

            mock_fetcher = MagicMock()
            if s["uids"] and isinstance(s["uids"][0], list):
                mock_fetcher.fetch_user_uid.side_effect = s["uids"]
            else:
                mock_fetcher.fetch_user_uid.return_value = s["uids"]

            if isinstance(s["gpg_key_side"], list):
                mock_fetcher.get_gpg_key_by_uid.side_effect = s["gpg_key_side"]
            else:
                mock_fetcher.get_gpg_key_by_uid.return_value = s["gpg_key_side"]
            mock_fetcher_class.return_value = mock_fetcher

            mock_gpg = MagicMock()
            mock_import_result = MagicMock()
            mock_import_result.returncode = s["import_code"]
            mock_import_result.stderr = "gpg: no valid OpenPGP data found" if s["import_code"] != 0 else ""
            mock_gpg.import_keys.return_value = mock_import_result
            mock_gpg_class.return_value = mock_gpg

            if s["expected_exit"]:
                with pytest.raises(SystemExit) as exc_info:
                    checkout_last_signed_commit.main(["-t", "fake_dir", "-u", "https://example.com/repo.git"])
                assert exc_info.value.code == s["expected_exit"]
            else:
                checkout_last_signed_commit.main(["-t", "fake_dir", "-u", "https://example.com/repo.git"])
                mock_checker.repo_instance.git.checkout.assert_called_once_with(s["expected_checkout"])

    def test_main_execution(self) -> None:
        import sys

        orig_argv = sys.argv
        sys.argv = ["checkout_last_signed_commit.py"]

        script_content = pathlib.Path("checkout_last_signed_commit.py").read_text(encoding="utf-8")
        code = compile(script_content, "checkout_last_signed_commit.py", "exec")

        with pytest.raises(SystemExit):
            exec(code, {"__name__": "__main__"})  # noqa: S102

        sys.argv = orig_argv
