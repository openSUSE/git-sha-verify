# Copyright SUSE LLC
import unittest
from unittest.mock import MagicMock, Mock, patch

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
        assert commit_checker.fetch_depth == 2
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


if __name__ == "__main__":
    unittest.main()
