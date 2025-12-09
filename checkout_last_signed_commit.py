# Copyright SUSE LLC

"""check_last_signed_commit checks out a verified git commit to be used mainly in CI pipelines.

check_last_signed_commit is written to be used mainly in GitLab CI Pipeline,
it is used to check out a verified git commit to be later used in deploy stage.
This script is meant to verify git commits coming from external VCS provider
such as GitHub, bitbucket etc. At present, it can only check out and verify
commits from only those SUSE employees whose public GPG keys are uploaded in
GitLab and can be fetched using GET request on GitLab user API endpoint
https://gitlab.suse.de/api/v4/users/$uid/gpg_keys.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import git
import gnupg
import requests

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

GITLAB_USER_API_DEFAULT = "https://gitlab.suse.de/api/v4/users/"
INITIAL_GIT_FETCH_DEPTH = 2

"""
Depth value 2147483647 (or 0x7fffffff, the largest positive number a signed 32-bit integer can contain)
means infinite depth, refer https://git-scm.com/docs/shallow
"""
GIT_FETCH_DEPTH_LIMIT = 2147483647
FETCH_NO_NEW_COMMITS_REGEX = "remote:.*Total 0 .*"


class GitLabGPGKeyFetcher:
    """GPG Key fetcher class functionalities to fetch GPG key by unique user identifier.

    Supports fetching user id by user email and username.

    Parameters
    ----------
        user_email (str): User email ID.
        user_api_url (str): Service provider (such as GitLab) API endpoint for fetching user information.
        private_token (str): Service provider (such as GitLab) private token for API authentication.

    """

    def __init__(
        self, user_email: str | None = None, user_api_url: str | None = None, private_token: str | None = None
    ) -> None:
        self.private_token = private_token or os.environ.get("PRIVATE_TOKEN")
        if not self.private_token:
            err_msg = "Please set env var PRIVATE_TOKEN for GitLab User API Authentication"
            logger.error(err_msg)
            sys.exit(err_msg)

        if user_api_url is None:
            self.user_api_url = GITLAB_USER_API_DEFAULT
        else:
            self.user_api_url = user_api_url
        self.user_email = user_email

    def get_gpg_key_by_uid(self, uid: int | None = None) -> str | None:
        """Fetch the GPG public key for a given GitLab user ID."""
        gpg_key = None
        if uid is not None:
            try:
                response = requests.get(  # noqa: S113 request-without-timeout
                    url=self.user_api_url + str(uid) + "/gpg_keys", headers={"PRIVATE-TOKEN": self.private_token}
                )
                response.raise_for_status()
                if response.status_code == 200 and len(response.json()) != 0:
                    gpg_key = response.json()[0].get("key")
            except requests.exceptions.HTTPError:
                logger.exception("HTTP Error")
            except requests.exceptions.RequestException:
                logger.exception("An error occurred")
        return gpg_key

    def fetch_user_uid(self, email: str | None = None) -> list[int]:
        """Return a list of user IDs matching a given email (or self.user_email)."""
        email = email or self.user_email
        if not email:
            return []

        logger.debug("Looking up UID by email: %s", email)
        singular_ids = self._search_user_ids(term=email)
        if not singular_ids:
            base_name = email.split("@")[0].split(".")[0]
            singular_ids = self._fetch_user_uid_by_name(base_name)
            logger.debug("UIDs derived by name %s: %s", base_name, singular_ids)

        return singular_ids

    def _fetch_user_uid_by_name(self, name: str | None = None) -> list[int]:
        """Return a list of user IDs matching a given name.

        If name is None and user_email is set, use the local-part of the email.
        """
        if name is None and self.user_email:
            name = self.user_email.split("@")[0]
        logger.debug("Looking up UID by name: %s", name)
        if not name:
            return []

        return self._search_user_ids(term=name)

    # --------- internal helpers --------- #
    def _search_user_ids(self, term: str) -> list[int]:
        """Search GitLab users by term and return a list of IDs."""
        try:
            response = requests.get(
                url=f"{self.user_api_url}?search={term}",
                headers={"PRIVATE-TOKEN": self.private_token},
                timeout=10,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException:
            logger.exception("Failed searching GitLab users for term=%s", term)
            return []

        data = response.json()
        return [entry.get("id") for entry in data or []]


class GitCheckVerifiedCommit:
    # ruff: noqa: E501
    """Checking out verified commit class functionalities to check GPG commit signature before checking out the git commit.

    Parameters
    ----------
        target_dir (str): Target directory where to clone or checkout the repository.
        repo_url (str): Git Repository URL.

    """

    def __init__(self, target_dir: str | None = None, repo_url: str | None = None) -> None:
        self.path_to_checkout_dir = target_dir
        self.repository_url = repo_url
        self.fetch_args = None
        self.commit_sha = None
        self.uid = None
        self.repo_instance = None

    def create_checkout_dir(self) -> str | Exception | None:
        """Create the target directory if it doesn't exist."""
        dir_path = self.path_to_checkout_dir
        if self.path_to_checkout_dir is not None:
            try:
                Path(str(self.path_to_checkout_dir)).mkdir(mode=0o766, parents=True, exist_ok=True)
            except Exception as e:
                dir_path = e
                logger.exception("An error occurred")
        return dir_path

    def init_or_load_repo(self) -> None:
        """Initialize a new repo or loads an existing one."""
        path = str(self.path_to_checkout_dir) + "/.git"
        if not Path(path).exists() or not Path(path).is_dir():
            if self.repository_url is None:
                err_msg = f"No previous git checkout at {self.path_to_checkout_dir} and no URL provided"
                logger.error(err_msg)
                sys.exit(err_msg)

            logger.info("Initializing repo...")
            self.repo_instance = git.Repo.init(self.path_to_checkout_dir, initial_branch="main")
            self.repo_instance.create_remote("origin", url=self.repository_url, tags=False)
            self.repo_instance.config_writer().set_value('gpg "ssh"', "allowedSignersFile", "/dev/null").release()
        else:
            logger.info("Using existing repo at path: %s", path)
            self.repo_instance = git.Repo(path)

    def fetch_git_repo(self, depth_val: int = INITIAL_GIT_FETCH_DEPTH) -> str | None:
        """Fetch the remote repo with specified depth. Return True if new commits were fetched, False otherwise."""
        num_jobs = os.cpu_count()
        if self.repo_instance is not None:
            if depth_val == INITIAL_GIT_FETCH_DEPTH:
                self.fetch_args = {"depth": depth_val}
            else:
                self.fetch_args = {"deepen": min(GIT_FETCH_DEPTH_LIMIT, depth_val)}

            fetcher_info = self.repo_instance.git.fetch(
                "origin",
                "--no-tags",
                "--no-show-forced-updates",
                with_extended_output=True,
                progress=True,
                jobs=(num_jobs - 1) if num_jobs is not None and num_jobs >= 3 else 1,
                **self.fetch_args,
            )
            logger.debug("Status: %s", fetcher_info[0])
            logger.debug("stdout %s", fetcher_info[1])
            logger.debug("stderr: %s", fetcher_info[2])
            return fetcher_info[2]
        return None

    def get_default_remote_branch(self) -> str | None:
        """Determine the default branch name from the remote 'origin'."""
        default_branch = None
        if self.repo_instance is not None:
            head_branch = self.repo_instance.git.remote("show", "origin")
            matches = re.search(r"\s*HEAD branch:\s*(.*)", head_branch)
            if matches:
                default_branch = matches.group(1)
        logger.debug("Def Branch: %s", default_branch)
        return default_branch

    def get_commiter_email(self, git_branch: str | None = None) -> list:
        """Get unique committer emails for a given branch ref, filtered by 'suse' committer."""
        emails = []
        if git_branch is not None:
            ref_name = "origin/" + str(git_branch)
        else:
            ref_name = "origin/" + str(self.get_default_remote_branch())

        if self.repo_instance is not None:
            for commit in self.repo_instance.iter_commits(ref_name, committer="suse"):
                emails.append(commit.committer.email)  # noqa: PERF401 use list.extend
        return sorted(set(emails))

    def get_signed_commit_sha(self, git_branch: str | None = None) -> str | None:
        """Search the git log for the most recent commit with a Good (G) or Unknown (U) GPG signature."""
        commit_sha = None
        ref_name = "origin/" + str(git_branch)
        if self.repo_instance is not None:
            log_op = self.repo_instance.git.log(ref_name, '--pretty="%G? %H"')
            regex_search = re.search(r"(?<=[UG] )[a-fA-F0-9]*", log_op)
            if regex_search is not None:
                commit_sha = regex_search.group(0)
        return commit_sha


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Checkout latest GPG-signed commit from a git repository",
    )
    parser.add_argument(
        "-t",
        "--target_dir",
        required=True,
        help="Path to existing git dir or new checkout dir",
    )
    parser.add_argument(
        "-u",
        "--url",
        required=True,
        help="Remote URL of git repository ending with .git",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:  # noqa: C901 complex-structure
    unique_ids = []
    signed_commit_sha = None
    default_remote_branch = None
    gpg_keys_imported = []
    gpg_keys_not_found = []
    git_fetch_depth = INITIAL_GIT_FETCH_DEPTH

    args = parse_args(argv)

    git_repo = GitCheckVerifiedCommit(args.target_dir, args.url)
    git_repo.create_checkout_dir()
    git_repo.init_or_load_repo()

    default_remote_branch = git_repo.get_default_remote_branch()
    logger.info("Default Branch: %s", default_remote_branch)

    private_token = os.environ.get("PRIVATE_TOKEN")
    gitlab_key_fetcher = GitLabGPGKeyFetcher(private_token=private_token)
    gpg_instance = gnupg.GPG()
    while signed_commit_sha is None:  # noqa: PLR1702 too-many-nested-blocks
        fetch_output = git_repo.fetch_git_repo(git_fetch_depth)
        regx_search = re.search(FETCH_NO_NEW_COMMITS_REGEX, fetch_output)
        if regx_search is not None:
            err_msg = "No new commits found on server"
            logger.error(err_msg)
            sys.exit(err_msg)
        elif git_fetch_depth >= GIT_FETCH_DEPTH_LIMIT:
            logger.error("Cannot find a verified commit in last %s commits", git_fetch_depth)
            break

        emails = git_repo.get_commiter_email(default_remote_branch)
        for e in emails:
            if e not in gpg_keys_imported and e not in gpg_keys_not_found:
                unique_ids = gitlab_key_fetcher.fetch_user_uid(e)
                for uid in unique_ids:
                    gpg_key = gitlab_key_fetcher.get_gpg_key_by_uid(uid)
                    if gpg_key is not None:
                        import_result = gpg_instance.import_keys(gpg_key)
                        regx_search = re.search(r"gpg: no valid OpenPGP data found", import_result.stderr)
                        if import_result.returncode == 0 and regx_search is None:
                            gpg_keys_imported.append(e)
                            gpg_keys_imported = sorted(set(gpg_keys_imported))
                        else:
                            logger.error("no valid OpenPGP data found for uid: %s", uid)

                        signed_commit_sha = git_repo.get_signed_commit_sha(default_remote_branch)
                        if signed_commit_sha is not None:
                            logger.info("Got Signed Commit SHA: %s", signed_commit_sha)
                            git_repo.repo_instance.git.checkout(signed_commit_sha)
                            unique_ids.clear()
                            emails.clear()
                            break
                    else:
                        gpg_keys_not_found.append(e)
                        gpg_keys_not_found = sorted(set(gpg_keys_not_found))
            else:
                if gpg_keys_imported:
                    logger.debug("GPG Keys already Imported for %s", gpg_keys_imported)
                if gpg_keys_not_found:
                    logger.debug("GPG Keys not found for %s", gpg_keys_not_found)
        git_fetch_depth *= 2


if __name__ == "__main__":
    main()
