import pytest
import checkout_last_signed_commit


def test_git_lab_gpg_key_fetcher_uninitialized(monkeypatch):
    with pytest.raises(SystemExit) as exc_info:
        gpg_key_fetcher = checkout_last_signed_commit.GitLabGPGKeyFetcher()
    assert exc_info.type == SystemExit
    assert exc_info.value.code == 'Please set the environment variable PRIVATE_TOKEN for GitLab User API Authentication'

    monkeypatch.setenv('PRIVATE_TOKEN', 'my_temporary_value')
    gpg_key_fetcher = checkout_last_signed_commit.GitLabGPGKeyFetcher()
    assert gpg_key_fetcher.user_api_url == checkout_last_signed_commit.gitlab_user_api
    assert gpg_key_fetcher.user_email is None
    assert gpg_key_fetcher.get_gpg_key_by_uid() is None
    assert gpg_key_fetcher.fetch_user_uid_by_email() == []
    assert gpg_key_fetcher.fetch_user_uid_by_name() == []


def test_checkout_verified_commit_uninitialized():
    commit_checker = checkout_last_signed_commit.CheckoutVerifiedCommit()
    assert commit_checker.fetch_depth == 2
    assert commit_checker.path_to_checkout_dir is None
    assert commit_checker.repository_url is None
    assert commit_checker.commit_sha is None
    assert commit_checker.uid is None
    assert commit_checker.repo_instance is None
    assert commit_checker.create_checkout_dir() == commit_checker.path_to_checkout_dir

    with pytest.raises(SystemExit) as exc_info:
        commit_checker.init_or_load_repo()
    assert exc_info.type == SystemExit
    assert exc_info.value.code == f'No previous git checkout at {commit_checker.path_to_checkout_dir} and no URL provided'
    assert commit_checker.get_default_remote_branch() is None
    assert commit_checker.get_commiter_email() == []
    assert commit_checker.get_signed_commit_sha() is None
