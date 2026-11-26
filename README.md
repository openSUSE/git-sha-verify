# git-sha-verify

[![CI](https://github.com/openSUSE/git-sha-verify/actions/workflows/python-app.yml/badge.svg)](https://github.com/openSUSE/git-sha-verify/actions/workflows/python-app.yml)

A simple utility to verify and check out trusted git commits signed using SSH/GPG key.  
This tool helps ensure that only authorized or validated commit hashes are checked out from a git repository, supporting better code integrity and security within the workflow.

## Usage

### Quickstart on SSH commit signing

SSH commit signing has the advantage that every user of Git already has an SSH key that can be used to sign commits.
These keys are even already uploaded to their GitLab profiles.
```
# global options
git config --global user.signingkey ~/.ssh/id_ed25519.pub
touch ~/.ssh/allowed_signers
git config --global gpg.ssh.allowedSignersFile ~/.ssh/allowed_signers
# add your own key to the allowed_signers (just as an example)
echo "$(git config get user.email) namespaces=\"git\" $(cat $(git config user.signingkey))" >> ~/.ssh/allowed_signers

# ssh-sign all commits in a repo
git config gpg.format ssh
git config commit.gpgsign true

# verify commits
git log --show-signatures
```

Signatures can be checked via `git log --show-signature`.

### Running with openSUSE system packages

`checkout-latest-signed-commit` runs with the system Python interpreter and needs no virtual environment:

```
zypper install git-core gpg2 openssh-clients python3-GitPython python3-requests python3-python-gnupg
```

The same packages are installed by `container/Containerfile`.

Options:
* `PRIVATE_TOKEN`: GitLab token with `read_api` scope, required to fetch user keys from GitLab unless `-n` is given.
* `-k/--key-types {ssh,gpg}`: signature types to accept, repeatable. Defaults to `ssh` and `gpg`.
* `-n/--no-key-sync`: skip syncing keys from GitLab, implies `-p`, conflicts with `-c`.

```
PRIVATE_TOKEN=<token> ./checkout-latest-signed-commit -t /path/to/checkout -u https://github.com/openSUSE/git-sha-verify.git
```

### Filtered mirror

A filtered mirror of this repository only admits commits up to the newest one whose signature verifies against the keys configured for that mirror. The `.gitlab-ci.yml` in this repository defines the mirror job: it runs the checkout script against GitHub and pushes the result fast-forward only. The image is pinned by digest in `.gitlab-ci.yml` and built from `container/Containerfile`.

Commits are admitted by signature alone. Sign-off commits (`chore: sign current state after <sha>`) are an exception: use them only when a deliberate block of unsigned or web-flow commits must be admitted into the checkout, never as a routine step.

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

Ensure you have the dependencies for development installed. When `uv` is
installed, `make test`, `make checkstyle`, and `make tidy` automatically
execute tools through the locked environment (`uv run --locked`),
guaranteeing pinned versions of ruff and other developer dependencies.

Alternatively if you are in an environment without `uv`, `make` targets
fall back to tools on `$PATH` (override with `PYTHON_RUN=""` to force).

Run `make checkstyle` to check coding style and `make tidy` for automated
formatting.


## License

This project is licensed under the MIT license, see LICENSE file for details.
Some exceptions apply and are marked accordingly.
