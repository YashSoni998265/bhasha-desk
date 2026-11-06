"""Small git helpers. Everything shells out to git; nothing is cached."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from bhasha import BhashaError
from bhasha.catalog import Reader

AGENT_NAME = "Bhasha Desk agent"
AGENT_EMAIL = "bhasha-desk-agent@users.noreply.github.com"


def git(cwd: Path, *args: str, check: bool = True, env: Optional[dict] = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise BhashaError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def ok(cwd: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True).returncode == 0


def is_repo(cwd: Path) -> bool:
    return ok(cwd, "rev-parse", "--git-dir")


def has_remote(cwd: Path, name: str = "origin") -> bool:
    return name in git(cwd, "remote").split()


def fetch(cwd: Path) -> None:
    if has_remote(cwd):
        git(cwd, "fetch", "origin", "--prune", "--quiet")


def ref_exists(cwd: Path, ref: str) -> bool:
    return ok(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def best_ref(cwd: Path, branch: str) -> Optional[str]:
    """The remote branch if it exists (shared truth), else the local one."""
    for ref in (f"origin/{branch}", branch):
        if ref_exists(cwd, ref):
            return ref
    return None


def show(cwd: Path, ref: str, rel: str) -> Optional[str]:
    proc = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=str(cwd), capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def ref_reader(cwd: Path, ref: str) -> Reader:
    return lambda rel: show(cwd, ref, rel)


def clone_root(cwd: Path) -> Path:
    """The main checkout, even when cwd is one of its worktrees."""
    common = Path(git(cwd, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (cwd / common).resolve()
    return common.parent


def worktree_home(cwd: Path) -> Path:
    override = os.environ.get("BHASHA_WORKTREES")
    return Path(override).resolve() if override else clone_root(cwd).parent


def current_branch(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--abbrev-ref", "HEAD")


def head(cwd: Path, ref: str = "HEAD") -> str:
    return git(cwd, "rev-parse", "--short", ref)


def identity_env(cwd: Path) -> dict:
    """Commit as the configured user, or as the agent when nobody is configured."""
    name = subprocess.run(["git", "config", "user.name"], cwd=str(cwd), capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "config", "user.email"], cwd=str(cwd), capture_output=True, text=True).stdout.strip()
    name = name or os.environ.get("BHASHA_GIT_NAME", AGENT_NAME)
    email = email or os.environ.get("BHASHA_GIT_EMAIL", AGENT_EMAIL)
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def commit(cwd: Path, paths: list, message: str) -> Optional[str]:
    """Commit the given paths. Returns the short sha, or None if nothing changed."""
    if not is_repo(cwd):
        return None
    git(cwd, "add", "--", *[str(p) for p in paths])
    if ok(cwd, "diff", "--cached", "--quiet"):
        return None
    git(cwd, "commit", "--quiet", "-m", message, env=identity_env(cwd))
    return head(cwd)


def github_repo(cwd: Path) -> Optional[str]:
    """owner/name when origin is on GitHub, so a pull request can be opened."""
    if not has_remote(cwd):
        return None
    url = git(cwd, "remote", "get-url", "origin")
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(prefix):
            slug = url[len(prefix):]
            return slug[:-4] if slug.endswith(".git") else slug
    return None


def worktree_for(cwd: Path, branch: str) -> Optional[Path]:
    """The worktree that has `branch` checked out, if any."""
    current = None
    for line in git(cwd, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{branch}" and current is not None:
            return current
    return None
