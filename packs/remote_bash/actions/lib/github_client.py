"""
Clone GitHub repositories to a temporary directory without shell injection.

Uses subprocess with argv lists (never shell=True).
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import FrozenSet, Optional

LOG = logging.getLogger(__name__)


class GitHubClientError(Exception):
    """Raised when git operations fail or paths are invalid."""


class GitHubClient:
    """Shallow git clone helper with optional PAT authentication."""

    def __init__(self, github_token: Optional[str] = None, clone_timeout: int = 600) -> None:
        self.github_token = github_token
        self.clone_timeout = clone_timeout

    def clone_repo(self, repo_url: str, branch: str) -> Path:
        """
        Perform a shallow clone of ``repo_url`` checking out ``branch``.

        Returns:
            Path to temporary directory containing the repository (caller must delete).

        Raises:
            GitHubClientError: If git fails or the URL is unsupported.
        """
        safe_url = self._inject_token_if_needed(repo_url)
        tmpdir = Path(tempfile.mkdtemp(prefix="st2_remote_bash_git_"))
        LOG.info(
            "Starting shallow git clone",
            extra={"repo_host": self._host_only(safe_url), "branch": branch, "dest": str(tmpdir)},
        )
        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            safe_url,
            str(tmpdir / "repo"),
        ]
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.clone_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            self._rmtree_safe(tmpdir)
            raise GitHubClientError(f"git clone timed out after {self.clone_timeout}s") from exc
        except FileNotFoundError as exc:
            self._rmtree_safe(tmpdir)
            raise GitHubClientError("git executable not found on PATH") from exc

        if proc.returncode != 0:
            self._rmtree_safe(tmpdir)
            LOG.warning(
                "git clone failed",
                extra={
                    "returncode": proc.returncode,
                    "stderr_snippet": (proc.stderr or "")[:500],
                },
            )
            raise GitHubClientError(f"git clone failed with exit code {proc.returncode}")

        repo_root = tmpdir / "repo"
        if not repo_root.is_dir():
            self._rmtree_safe(tmpdir)
            raise GitHubClientError("clone did not produce expected directory layout")
        return tmpdir

    def resolve_under_repo(self, repo_root: Path, relative_path: str) -> Path:
        """Resolve a repository-relative path under ``repo_root/repo`` with containment."""
        inner = repo_root / "repo"
        candidate = (inner / relative_path).resolve()
        inner_resolved = inner.resolve()
        try:
            candidate.relative_to(inner_resolved)
        except ValueError as exc:
            raise GitHubClientError("path escapes repository root") from exc
        return candidate

    def get_repo_file(
        self,
        repo_root: Path,
        relative_path: str,
        allowed_suffixes: Optional[FrozenSet[str]] = None,
    ) -> Path:
        candidate = self.resolve_under_repo(repo_root, relative_path)
        if allowed_suffixes is not None:
            suf = candidate.suffix.lower()
            if suf not in allowed_suffixes:
                raise GitHubClientError(
                    f"file extension not permitted for this parameter: {relative_path}"
                )
        if not candidate.is_file():
            raise GitHubClientError(f"file not found or not a regular file: {relative_path}")
        return candidate

    def get_script_path(self, repo_root: Path, script_path: str) -> Path:
        candidate = self.resolve_under_repo(repo_root, script_path)
        if candidate.suffix.lower() != ".sh":
            raise GitHubClientError("script_path must end with .sh")
        if not candidate.is_file():
            raise GitHubClientError(f"script not found or not a file: {script_path}")
        return candidate

    def _inject_token_if_needed(self, repo_url: str) -> str:
        """For HTTPS GitHub URLs, embed PAT when configured (never logged)."""
        if not self.github_token:
            return repo_url
        if "github.com" not in repo_url:
            return repo_url
        if repo_url.startswith("git@") or repo_url.startswith("ssh://"):
            return repo_url
        if not repo_url.startswith("https://github.com/"):
            return repo_url
        tail = repo_url[len("https://") :]
        return f"https://x-access-token:{self.github_token}@{tail}"

    @staticmethod
    def _host_only(url: str) -> str:
        try:
            from urllib.parse import urlparse

            return urlparse(url).hostname or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _rmtree_safe(path: Path) -> None:
        import shutil

        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            LOG.warning("Failed to remove temp dir", extra={"path": str(path)})
