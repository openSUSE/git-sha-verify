# git-sha-verify

[![codecov](https://codecov.io/github/openSUSE/git-sha-verify/graph/badge.svg?token=aCargeDG7M)](https://codecov.io/github/openSUSE/git-sha-verify)

A simple utility to verify and checkout trusted git commits signed using GPG key.  
This tool helps ensure that only authorized or validated commit hashes are checked out from a git repository, supporting better code integrity and security within the workflow.

## Contribute

This project lives in https://github.com/openSUSE/git-sha-verify

Feel free to add issues in github or send pull requests.

### Rules for commits

* For git commit messages use the rules stated on
  [How to Write a Git Commit Message](http://chris.beams.io/posts/git-commit/)
  as a reference.
* Run `make tidy` before committing changes to format code according to our
  standards. Preferably also run other tests as described in the subsequent
  section.
* As a SUSE colleague consider signing commits which we consider to use for
  automatic deployments within SUSE.

If this is too much hassle for you feel free to provide incomplete pull requests
for consideration or create an issue with a code change proposal.

### Local testing

Ensure you have the dependencies for development installed. The easiest
way to get them is via uv:

    uv pip install -e ".[dev]"

Run `make test` or `pytest` to execute Python-based unit tests.

Run `make checkstyle` to check coding style and `make tidy` for automated
formatting.


## License

This project is licensed under the MIT license, see LICENSE file for details.
Some exceptions apply and are marked accordingly.
