#!/usr/bin/env python3
# Copyright SUSE LLC
# ruff: file-ignore[line-too-long]

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

GITLAB_USER_API_DEFAULT = "https://gitlab.suse.de/api/v4/users/"
INITIAL_GIT_FETCH_DEPTH = 2

# Depth 2147483647 (0x7fffffff, max signed 32-bit int) means infinite depth: https://git-scm.com/docs/shallow
GIT_FETCH_DEPTH_LIMIT = 0x7fffffff
FETCH_NO_NEW_COMMITS_REGEX = "remote:.*Total 0 .*"


def fatal_error(msg: str):
    logger.error(msg)
    sys.exit(1)

class GPG(gnupg.GPG):
    def clear_public_keys(self):
        private_fingerprints = {key["fingerprint"] for key in self.list_keys(secret=True)}

        for key in self.list_keys():
            fingerprint = key["fingerprint"]
            if fingerprint not in private_fingerprints:
                self.delete_keys(fingerprint)

class GitLabUsers:
    """ Fetch user_ids and a user's GPG/SSH public keys from GitLab

    Parameters
    ----------
        user_api_url:  URL to API endpoint for fetching user information.
        private_token: Token for API authentication.

    """

    def __init__(self, private_token: str | None = None, user_api_url: str = GITLAB_USER_API_DEFAULT) -> None:
        self.private_token = private_token
        self.user_api_url = user_api_url

    def get_pubkeys(self, user_id: int, key_type='ssh') -> list[str]:
        """Fetch the public keys for a given GitLab user ID."""
        try:
            res = self._api_request(f"{user_id}/{'gpg_' if key_type == 'gpg' else ''}keys")
            return [entry.get("key") for entry in res.json() if entry.get("key")]
        except requests.exceptions.RequestException:
            logger.exception("An error occurred")
            return []

    def get_user_ids(self, email: str) -> set[int]:
        """Return a set of user IDs matching a given email or at least parts of the name."""
        logger.debug("Looking up user ID by email: %s", email)
        user_ids = self._search_user_ids(email)
        if not user_ids:
            # For emails like john.doe@suse.com try to get the first name here
            first_name = email.split("@")[0].split(".")[0]
            user_ids = self._search_user_ids(first_name)
            logger.debug("user IDs by derived name %s: %s", first_name, user_ids)
        if not user_ids:
            # For emails like jdoe@suse.de try to get the last name here
            last_name = email.split("@")[0][1:]
            user_ids = self._search_user_ids(last_name)
            logger.debug("user IDs by derived name %s: %s", last_name, user_ids)
        return user_ids

    def _search_user_ids(self, term: str) -> set[int]:
        """Search GitLab users by name, username, or public email and return a set of user IDs."""
        try:
            res = self._api_request(f"?search={term}")
            return {entry.get("id") for entry in res.json() if entry.get("id")}
        except requests.exceptions.RequestException:
            logger.exception("Failed searching GitLab users for term=%s", term)
            return set()

    def _api_request(self, url_suffix: str, timeout: int = 10) -> requests.Response:
        params = {
            "url": f"{self.user_api_url}{url_suffix}",
            "timeout": timeout
        }
        if self.private_token:
            params['headers'] = {"PRIVATE-TOKEN": self.private_token}
        res = requests.get(**params)
        res.raise_for_status()
        return res


class GitRepo:
    """ Perform actions on a git repo.

    Parameters
    ----------
        checkout_dir: Target directory where to clone or checkout the repository.
        repo_url:     Git Repository URL.
        branch:       Remote branch to use (defaults to remote default branch)

    """

    def __init__(self, checkout_dir: str, repo_url: str | None = None, branch: str | None = None, clean_allowed_signers = False) -> None:
        self.checkout_dir = Path(checkout_dir)
        self.repo_url = repo_url

        # ensure that checkout dir exists with correct permissions
        self.checkout_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        git_dir = self.checkout_dir / '.git'
        self.allowed_signers_file = git_dir / 'allowed_signers_file'
        if not (git_dir.exists() and git_dir.is_dir()):
            if self.repo_url is None:
                fatal_error(f"No previous git checkout at {self.checkout_dir} and no URL provided")

            logger.info("Initializing repo...")
            self.repo = git.Repo.init(self.checkout_dir, initial_branch="main")
            self.repo.create_remote("origin", url=self.repo_url, tags=False)
            self.repo.config_writer().set_value('gpg "ssh"', "allowedSignersFile", str(self.allowed_signers_file)).release()
        else:
            logger.info("Using existing repo at path: %s", git_dir)
            self.repo = git.Repo(git_dir)
            if repo_url and repo_url != self.repo.remote('origin').url:
                fatal_error(f"Repo already present with different remote url: {self.repo.remote('origin').url}")

        self.allowed_signers_file.touch(mode=0o644, exist_ok=True)
        if clean_allowed_signers:
            open(self.allowed_signers_file, 'w').write('')
        self.ref = f"origin/{branch or self._get_remote_default_branch()}"
        logger.info("Using ref: %s", self.ref)

    def fetch(self, depth: int = INITIAL_GIT_FETCH_DEPTH) -> bool:
        """Fetch commits from remote repo with a specific depth. Return True if something was fetched."""
        cpus = os.cpu_count() or 1
        fetch_jobs = max(1, cpus - 1)
        fetcher_info = self.repo.git.fetch(
            "origin",
            "--no-tags",
            "--no-show-forced-updates",
            with_extended_output=True,
            progress=True,
            jobs=fetch_jobs,
            depth=min(GIT_FETCH_DEPTH_LIMIT, depth),
        )
        logger.debug("Status: %s", fetcher_info[0])
        logger.debug("stdout %s", fetcher_info[1])
        logger.debug("stderr: %s", fetcher_info[2])
        return not bool(re.search(FETCH_NO_NEW_COMMITS_REGEX, fetcher_info[2]))

    def get_committer_email_addrs(self) -> set[str]:
        """Returns set of committer email addresses, filtered by 'suse' committer."""
        return {commit.committer.email for commit in self.repo.iter_commits(self.ref, committer="suse") if commit.committer.email}

    def checkout_latest_signed_commit(self, key_types: list[str] = ['gpg', 'ssh']) -> str | None:
        """Search the git log for the most recent commit with a Good (G) or Unknown (U) signature.
           If a commit is found, checkout the commit and return the SHA, else return None.
        """
        commit_sha = None
        for commit in self.repo.git.log(self.ref, '--pretty="%H %G? %GK"').replace('"', '').split("\n"):
            sha, signature_status, signature_key = commit.split(' ')
            if ':' in signature_key and 'ssh' in key_types:
                if signature_status == 'G':
                    # valid SSH signatures must have status G
                    commit_sha = sha
                    break
            elif signature_status in ('G', 'U') and 'gpg' in key_types:
                # valid GPG signatures can be G or U
                commit_sha = sha
                break
        if commit_sha:
            self.repo.git.checkout(commit_sha)
        return commit_sha

    def import_ssh_keys(self, email_addr: str, ssh_keys: list[str]) -> None:
        allowed_signers = open(self.allowed_signers_file).read()
        with open(self.allowed_signers_file, 'a') as allowed_signers_file:
            for ssh_key in ssh_keys:
                if ssh_key.startswith('ssh-dss'):
                    logger.info("Ignoring deprecated ssh-dss key for %s", email_addr)
                line = f'{email_addr} namespaces="git" {ssh_key}\n'
                if line not in allowed_signers:
                    allowed_signers_file.write(line)

    def _get_remote_default_branch(self) -> str: # pyright: ignore[reportReturnType]
        """Determine the default branch name from the remote 'origin'."""
        default_branch = None
        head_branch = self.repo.git.remote("show", "origin")
        matches = re.search(r"\s*HEAD branch:\s*(.*)", head_branch)
        if matches:
            default_branch = matches.group(1)
            logger.debug("Def Branch: %s", default_branch)
            return default_branch
        fatal_error("Unable to determine remote default branch")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Checkout latest signed commit from a git repository",
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
        help="Remote URL of git repository. Defaults to URL of existing checkout if possible.",
    )
    parser.add_argument(
        "-b",
        "--branch",
        help="Checkout the specified branch instead of the remote default branch",
    )
    parser.add_argument(
        "-k",
        "--key_types",
        nargs='*',
        default=['ssh', 'gpg'],
        help="Key type to use (gpg or ssh). Both can be specified in parallel which is the default.",
    )
    parser.add_argument(
        "-c",
        "--clean_keystore",
        action='store_true',
        help="Cleanup the specified key stores first.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    processed_email_addrs = set()
    processed_user_ids = set()
    git_fetch_depth = INITIAL_GIT_FETCH_DEPTH

    args = parse_args(argv)

    git_repo = GitRepo(args.target_dir, args.url, args.branch, clean_allowed_signers=(args.clean_keystore and 'ssh' in args.key_types))
    gitlab_users = GitLabUsers(private_token=os.environ.get('PRIVATE_TOKEN'))
    gpg = GPG()

    if args.clean_keystore and 'gpg' in args.key_types:
        gpg.clear_public_keys()

    while git_fetch_depth < GIT_FETCH_DEPTH_LIMIT:
        # Fetch commits
        commits_fetched = git_repo.fetch(git_fetch_depth)
        if not commits_fetched:
            # Only exit in first iteration if there is a valid checkout
            if git_fetch_depth == INITIAL_GIT_FETCH_DEPTH and git_repo.repo.head.is_valid():
                logger.info("No new commits found on server")
                return
            if git_fetch_depth > INITIAL_GIT_FETCH_DEPTH:
                break

        # Import keys
        imported_new_keys = False
        new_email_addrs = git_repo.get_committer_email_addrs() - processed_email_addrs
        for email_addr in new_email_addrs:
            new_user_ids = gitlab_users.get_user_ids(email_addr) - processed_user_ids
            for user_id in new_user_ids:
                if 'gpg' in args.key_types:
                    if gpg_keys := gitlab_users.get_pubkeys(user_id, key_type='gpg'):
                        if gpg.import_keys("\n".join(gpg_keys)).count:
                            imported_new_keys = True
                        else:
                            logger.error("Could not import GPG key for user_id: %i", user_id)
                if 'ssh' in args.key_types:
                    if ssh_keys := gitlab_users.get_pubkeys(user_id, key_type='ssh'):
                        git_repo.import_ssh_keys(email_addr, ssh_keys)
                        imported_new_keys = True
            processed_user_ids |= new_user_ids
        processed_email_addrs |= new_email_addrs

        # Checkout commits
        if imported_new_keys:
            if sha := git_repo.checkout_latest_signed_commit(args.key_types):
                logger.info("Got Signed Commit SHA: %s", sha)
                return

        # Let's try again with more commits
        git_fetch_depth = min(git_fetch_depth * 2, GIT_FETCH_DEPTH_LIMIT)
    fatal_error(f"Cannot find a signed commit in last {git_fetch_depth} commits")

if __name__ == "__main__":
    main()
