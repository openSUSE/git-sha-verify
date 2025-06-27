#!/usr/bin/env python

"""

This script is written to be used mainly in GitLab CI Pipeline, it is used to
check out a verified git commit to be later used in deploy stage. This script
is meant to verify git commits coming from external VCS provider such as
GitHub, bitbucket etc. At present, it can only check out and verify commits
from only those SUSE employees whose public GPG keys are uploaded in GitLab
and can be fetched using GET request on GitLab user API endpoint
https://gitlab.suse.de/api/v4/users/$uid/gpg_keys
"""
import argparse
import os
import git
import gnupg
import logging
from pathlib import Path
import re
import requests
import sys

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

gitlab_user_api = 'https://gitlab.suse.de/api/v4/users/'
git_fetch_depth = int(2)


class GitLabGPGKeyFetcher:
    def __init__(self, user_email=None, user_api_url=None, private_token=None):
        self.private_token = private_token or os.environ.get('PRIVATE_TOKEN')
        if not self.private_token:
            err_msg = 'Please set the environment variable PRIVATE_TOKEN fot GitLab User API Authentication'
            logger.error(err_msg)
            sys.exit(err_msg)

        if user_api_url is None:
            self.user_api_url = gitlab_user_api
        else:
            self.user_api_url = user_api_url
        self.user_email = user_email

    def get_gpg_key_by_uid(self, uid=None):
        gpg_key = None
        if uid is not None:
            try:
                response = requests.get(url=self.user_api_url + str(uid) + '/gpg_keys',
                                        headers={'PRIVATE-TOKEN': self.private_token})
                response.raise_for_status()
                if response.status_code == 200 and len(response.json()) != 0:
                    gpg_key = response.json()[0].get('key')
            except requests.exceptions.HTTPError as e:
                logger.error(f'HTTP Error: {e}')
            except requests.exceptions.RequestException as e:
                logger.error(f'An error occurred: {e}')
        return gpg_key

    def fetch_user_uid_by_email(self, email=None):
        uid = []
        if email is None and self.user_email is not None:
            email = self.user_email

        logger.debug('Email: %s', email)
        if email is not None:
            try:
                response = requests.get(url=self.user_api_url + '?search=' + str(email),
                                        headers={'PRIVATE-TOKEN': self.private_token})
                if response.status_code == 200 and len(response.json()) != 0:
                    for index in range(0, len(response.json())):
                        uid.append(response.json()[index].get('id'))
            except requests.exceptions.HTTPError as e:
                logger.error(f'HTTP Error: {e}')
            except requests.exceptions.RequestException as e:
                logger.error(f'An error occurred: {e}')
        return uid

    def fetch_user_uid_by_name(self, name=None):
        uid = []
        if name is None and self.user_email is not None:
            name = self.user_email.split('@')[0]
        logger.debug('Name: %s', name)
        if name is not None:
            try:
                response = requests.get(url=self.user_api_url + '?search=' + str(name),
                                        headers={'PRIVATE-TOKEN': self.private_token})
                if response.status_code == 200 and len(response.json()) != 0:
                    for index in range(0, len(response.json())):
                        uid.append(response.json()[index].get('id'))
            except requests.exceptions.HTTPError as e:
                logger.error(f'HTTP Error: {e}')
            except requests.exceptions.RequestException as e:
                logger.error(f'An error occurred: {e}')
        return uid


class CheckoutVerifiedCommit:
    def __init__(self, target_dir=None, repo_url=None, fetch_depth=2):
        self.fetch_depth = fetch_depth
        self.path_to_checkout_dir = target_dir
        self.repository_url = repo_url
        self.commit_sha = None
        self.uid = None
        self.repo_instance = None

    def create_checkout_dir(self):
        dir_path = self.path_to_checkout_dir
        if self.path_to_checkout_dir is not None:
            try:
                Path(str(self.path_to_checkout_dir)).mkdir(mode=766, parents=True, exist_ok=True)
            except Exception as e:
                dir_path = e
                logger.error(f'An error occurred: {e}')
        return dir_path

    def init_get_repo(self):
        path = str(self.path_to_checkout_dir) + '/.git'
        if not Path(path).exists() or not Path(path).is_dir():
            if self.repository_url is None:
                err_msg = f'No previous git checkout at {self.path_to_checkout_dir} and no URL provided'
                logger.error(err_msg)
                sys.exit(err_msg)

            logger.info('Initializing repo...')
            self.repo_instance = git.Repo.init(self.path_to_checkout_dir, initial_branch='main')
            self.repo_instance.create_remote('origin', url=self.repository_url, tags=False)
            self.repo_instance.config_writer().set_value('gpg "ssh"', 'allowedSignersFile', '/dev/null').release()
        else:
            logger.info('Using existing repo at path: %s', path)
            self.repo_instance = git.Repo(path)

    def fetch_git_repo(self, depth_val=2):
        if self.repo_instance is not None:
            self.fetch_depth = depth_val
            logger.info('Fetching with depth %s', self.fetch_depth)
            fetcher_info = self.repo_instance.git.fetch('origin', depth=self.fetch_depth, with_extended_output=True,
                                                        progress=True)
            logger.debug('Status: %s', str(fetcher_info[0]))
            logger.debug('stdout %s', str(fetcher_info[1]))
            logger.debug('stderr: %s', str(fetcher_info[2]))
            return fetcher_info[2]

    def get_default_remote_branch(self):
        default_branch = None
        if self.repo_instance is not None:
            head_branch = self.repo_instance.git.remote('show', 'origin')
            matches = re.search(r'\s*HEAD branch:\s*(.*)', head_branch)
            if matches:
                default_branch = matches.group(1)
        logger.debug('Def Branch: %s', default_branch)
        return default_branch

    def get_commiter_email(self, git_branch=None):
        emails = []
        if git_branch is not None:
            ref_name = 'origin/' + str(git_branch)
        else:
            ref_name = 'origin/' + str(self.get_default_remote_branch())

        if self.repo_instance is not None:
            commits = self.repo_instance.iter_commits(ref_name, committer='suse')
            for commit in commits:
                emails.append(commit.committer.email)
        return sorted(set(emails))

    def get_signed_commit_sha(self, git_branch=None):
        commit_sha = None
        ref_name = 'origin/' + str(git_branch)
        if self.repo_instance is not None:
            log_op = self.repo_instance.git.log(ref_name, '--pretty="%G? %H"')
            regex_search = re.search('(?<=[UG] )[a-fA-F0-9]*', log_op)
            if regex_search is not None:
                commit_sha = regex_search.group(0)
        return commit_sha


def main():
    unique_ids = []
    signed_commit_sha = None
    default_remote_branch = None
    gpg_keys_imported = []
    global git_fetch_depth
    fetch_regex = 'remote:.*Total 0 .*'

    arg_parser = argparse.ArgumentParser(description='Checkout Latest Signed Commit inside a git repository')
    arg_parser.add_argument('-t', '--target_dir', help='Path to existing git dir or new checkout dir', required=True)
    arg_parser.add_argument('-u', '--url', help='Remote URL of git repository ending with .git', required=True)

    args = arg_parser.parse_args()

    git_repo = CheckoutVerifiedCommit(args.target_dir, args.url)
    git_repo.create_checkout_dir()
    git_repo.init_get_repo()

    default_remote_branch = git_repo.get_default_remote_branch()
    logger.info('Default Branch: %s', default_remote_branch)

    private_token = os.environ.get('PRIVATE_TOKEN')
    gitlab_key_fetcher = GitLabGPGKeyFetcher(private_token=private_token)
    gpg_instance = gnupg.GPG()
    while signed_commit_sha is None:
        fetch_output = git_repo.fetch_git_repo(git_fetch_depth)
        regx_search = re.search(fetch_regex, fetch_output)
        if regx_search is not None and git_repo.fetch_depth == 2:
            err_msg = 'No new commits found on server'
            logger.error(err_msg)
            sys.exit(err_msg)

        emails = git_repo.get_commiter_email(default_remote_branch)
        for e in emails:
            if e not in gpg_keys_imported:
                unique_ids = gitlab_key_fetcher.fetch_user_uid_by_email(e)
                logger.debug('UID by Email: %s', unique_ids)
                if not len(unique_ids):
                    unique_ids = gitlab_key_fetcher.fetch_user_uid_by_name(e.split('@')[0].split('.')[0])
                    logger.debug('UID by name: %s', unique_ids)

                for uid in unique_ids:
                    gpg_key = gitlab_key_fetcher.get_gpg_key_by_uid(uid)
                    if gpg_key is not None:
                        import_result = gpg_instance.import_keys(gpg_key)
                        regx_search = re.search('gpg: no valid OpenPGP data found', import_result.stderr)
                        if import_result.returncode == 0 and regx_search is None:
                            gpg_keys_imported.append(e)
                        else:
                            logger.error('no valid OpenPGP data found for uid: %s', uid)

                        signed_commit_sha = git_repo.get_signed_commit_sha(default_remote_branch)
                        if signed_commit_sha is not None:
                            logger.info('Got Signed Commit SHA: %s', signed_commit_sha)
                            git_repo.repo_instance.git.checkout(signed_commit_sha)
                            unique_ids.clear()
                            emails.clear()
                            break
        git_fetch_depth *= 2


if __name__ == '__main__':
    main()
