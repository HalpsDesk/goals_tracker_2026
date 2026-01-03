"""
deploy/deploy_to_gh_pages.py

Deploy a built static site folder to the `gh-pages` branch of the *same* repo.

Design goals:
- No extra dependencies (uses git CLI via subprocess)
- Safe(ish) behavior: refuses to run if working tree is dirty
- Always pushes to gh-pages (per user preference)
- Restores original branch at the end

Typical flow:
1) build_site(db_path=..., out_dir=...)
2) deploy_site(out_dir=..., repo_dir=Path("."))

Notes:
- Authentication is handled by your existing git configuration.
  If `git push` works for you normally, this will work too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class DeployResult:
    pushed: bool
    commit_made: bool
    commit_sha: Optional[str]
    message: str


class DeployError(RuntimeError):
    pass


def deploy_site(
    *,
    out_dir: Path,
    repo_dir: Path = Path("."),
    branch: str = "gh-pages",
    remote: str = "origin",
    commit_message: Optional[str] = None,
) -> DeployResult:
    """
    Copy contents of out_dir into gh-pages branch, commit, and push.

    Requirements:
- repo_dir must be a git repo
- working tree must be clean (no uncommitted changes)
- out_dir must exist and contain generated site files
    """
    repo_dir = Path(repo_dir).resolve()
    out_dir = Path(out_dir).resolve()

    if not out_dir.exists() or not out_dir.is_dir():
        raise DeployError(f"out_dir does not exist or is not a directory: {out_dir}")

    _require_git()
    _require_repo(repo_dir)

    # Safety: refuse if working tree dirty
    if _is_dirty(repo_dir):
        raise DeployError(
            "Refusing to deploy because working tree has uncommitted changes.\n"
            "Commit/stash your changes first so we don't lose work when switching branches."
        )

    original_branch = _current_branch(repo_dir)

    # Default commit message
    if not commit_message:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f"Update site: {ts}"

    try:
        _checkout_branch(repo_dir, branch=branch, remote=remote)

        # Ensure branch is up-to-date with remote if it exists
        _git(repo_dir, ["pull", remote, branch], check=False)

        # Replace working tree contents with out_dir contents
        _replace_repo_contents_with_out_dir(repo_dir, out_dir)

        # Stage all changes
        _git(repo_dir, ["add", "-A"])

        # If no changes, skip commit and push
        if not _has_staged_changes(repo_dir):
            return DeployResult(
                pushed=False,
                commit_made=False,
                commit_sha=None,
                message="No changes detected in site output; nothing to commit/push.",
            )

        # Commit
        _git(repo_dir, ["commit", "-m", commit_message])
        commit_sha = _git_stdout(repo_dir, ["rev-parse", "HEAD"]).strip()

        # Push
        _git(repo_dir, ["push", remote, branch])

        return DeployResult(
            pushed=True,
            commit_made=True,
            commit_sha=commit_sha,
            message=f"Deployed to {remote}/{branch} at {commit_sha}",
        )

    finally:
        # Always attempt to return to original branch
        try:
            if original_branch:
                _git(repo_dir, ["checkout", original_branch], check=False)
        except Exception:
            # Last-resort: don't hide the original error; just best-effort cleanup
            pass


# -----------------------------
# Internal helpers
# -----------------------------

def _require_git() -> None:
    try:
        subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    except Exception as e:
        raise DeployError("git is not available on PATH. Install Git and restart VS Code.") from e


def _require_repo(repo_dir: Path) -> None:
    try:
        _git(repo_dir, ["rev-parse", "--is-inside-work-tree"])
    except Exception as e:
        raise DeployError(f"Not a git repository: {repo_dir}") from e


def _git(repo_dir: Path, args: List[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=check,
    )


def _git_stdout(repo_dir: Path, args: List[str]) -> str:
    cp = _git(repo_dir, args)
    return cp.stdout


def _is_dirty(repo_dir: Path) -> bool:
    # Includes untracked files too (important before branch switching)
    cp = _git(repo_dir, ["status", "--porcelain"], check=True)
    return bool(cp.stdout.strip())


def _current_branch(repo_dir: Path) -> str:
    # Returns "HEAD" if detached; still fine
    return _git_stdout(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def _branch_exists_local(repo_dir: Path, branch: str) -> bool:
    cp = _git(repo_dir, ["show-ref", "--verify", f"refs/heads/{branch}"], check=False)
    return cp.returncode == 0


def _branch_exists_remote(repo_dir: Path, remote: str, branch: str) -> bool:
    cp = _git(repo_dir, ["ls-remote", "--heads", remote, branch], check=False)
    return cp.returncode == 0 and bool(cp.stdout.strip())


def _checkout_branch(repo_dir: Path, *, branch: str, remote: str) -> None:
    if _branch_exists_local(repo_dir, branch):
        _git(repo_dir, ["checkout", branch])
        return

    # If remote exists, create local tracking branch
    if _branch_exists_remote(repo_dir, remote, branch):
        _git(repo_dir, ["checkout", "-b", branch, f"{remote}/{branch}"])
        return

    # Otherwise create an orphan branch for gh-pages
    _git(repo_dir, ["checkout", "--orphan", branch])
    # Remove any staged files created by orphan checkout
    _git(repo_dir, ["reset", "--hard"], check=False)


def _has_staged_changes(repo_dir: Path) -> bool:
    cp = _git(repo_dir, ["diff", "--cached", "--name-only"], check=True)
    return bool(cp.stdout.strip())


def _replace_repo_contents_with_out_dir(repo_dir: Path, out_dir: Path) -> None:
    """
    On gh-pages branch:
    - Delete everything in repo root except .git
    - Copy contents of out_dir into repo root
    """
    # Delete everything except .git
    for child in repo_dir.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    # Copy out_dir contents into repo root
    for src in out_dir.iterdir():
        dest = repo_dir / src.name
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
