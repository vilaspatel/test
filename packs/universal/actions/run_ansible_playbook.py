"""
Clone a Git repo, resolve an Ansible playbook + inventory path, and execute ansible-playbook.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from st2common.runners.base_action import Action

from lib.github_client import GitHubClient, GitHubClientError

PLAYBOOK_SUFFIXES: FrozenSet[str] = frozenset({".yml", ".yaml"})
INVENTORY_SUFFIXES: FrozenSet[str] = frozenset({".ini", ".yml", ".yaml", ".json"})


def _validate_repo_relative_path(param_name: str, raw_path: str, allowed_suffixes: FrozenSet[str]) -> None:
    if not raw_path or not str(raw_path).strip():
        raise ValueError(f"{param_name} is required")
    clean = str(raw_path).strip()
    posix = PurePosixPath(clean)
    if posix.is_absolute():
        raise ValueError(f"{param_name} must not be absolute")
    if ".." in posix.parts:
        raise ValueError(f"{param_name} must not include path traversal (../)")
    if posix.suffix.lower() not in allowed_suffixes:
        raise ValueError(f"{param_name} must end with one of {sorted(allowed_suffixes)}")


def _normalize_str_list(name: str, value: Optional[List[Any]]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array/list")
    return [str(v) for v in value if str(v).strip()]


class RunAnsiblePlaybookAction(Action):
    """Run ansible-playbook against a Git-hosted playbook and inventory file."""

    def run(
        self,
        github_repo: str,
        github_branch: str,
        playbook_path: str,
        inventory_path: str,
        ssh_username: Optional[str] = None,
        ssh_private_key: Optional[str] = None,
        ssh_port: Optional[int] = None,
        forks: Optional[int] = None,
        extra_vars: Optional[Any] = None,
        limit: Optional[str] = None,
        tags: Optional[List[Any]] = None,
        skip_tags: Optional[List[Any]] = None,
        check_mode: Optional[bool] = False,
        diff_mode: Optional[bool] = False,
        disable_host_key_checking: Optional[bool] = False,
        timeout: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        cfg = self.config or {}
        github_token = (cfg.get("github_token") or "").strip() or None
        clone_timeout = int(cfg.get("git_clone_timeout") or 600)
        exec_timeout = int(timeout) if timeout is not None else 3600

        try:
            _validate_repo_relative_path("playbook_path", playbook_path, PLAYBOOK_SUFFIXES)
            _validate_repo_relative_path("inventory_path", inventory_path, INVENTORY_SUFFIXES)
            tags_list = _normalize_str_list("tags", tags)
            skip_tags_list = _normalize_str_list("skip_tags", skip_tags)
            forks_int = int(forks) if forks is not None else None
            if forks_int is not None and forks_int < 1:
                raise ValueError("forks must be >= 1")
        except ValueError as exc:
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}

        key_file: Optional[str] = None
        tmp_git_parent: Optional[Path] = None
        try:
            gh = GitHubClient(github_token=github_token, clone_timeout=clone_timeout)
            tmp_git_parent = gh.clone_repo(github_repo.strip(), github_branch.strip())
            playbook_file = gh.get_repo_file(tmp_git_parent, playbook_path.strip(), PLAYBOOK_SUFFIXES)
            inventory_file = gh.get_repo_file(tmp_git_parent, inventory_path.strip(), INVENTORY_SUFFIXES)

            cmd: List[str] = [
                "ansible-playbook",
                "-i",
                str(inventory_file),
                str(playbook_file),
            ]

            if ssh_username:
                cmd.extend(["-u", ssh_username])

            if ssh_private_key and str(ssh_private_key).strip():
                with tempfile.NamedTemporaryFile(mode="w", prefix="st2_ssh_key_", delete=False) as handle:
                    handle.write(ssh_private_key)
                    key_file = handle.name
                Path(key_file).chmod(0o600)
                cmd.extend(["--private-key", key_file])

            if ssh_port is not None:
                cmd.extend(["--extra-vars", f"ansible_port={int(ssh_port)}"])
            if forks_int is not None:
                cmd.extend(["--forks", str(forks_int)])

            if limit and str(limit).strip():
                cmd.extend(["--limit", str(limit).strip()])
            if tags_list:
                cmd.extend(["--tags", ",".join(tags_list)])
            if skip_tags_list:
                cmd.extend(["--skip-tags", ",".join(skip_tags_list)])
            if check_mode:
                cmd.append("--check")
            if diff_mode:
                cmd.append("--diff")

            if extra_vars is not None:
                if isinstance(extra_vars, (dict, list)):
                    rendered = json.dumps(extra_vars)
                else:
                    rendered = str(extra_vars)
                if rendered.strip():
                    cmd.extend(["--extra-vars", rendered])

            env = None
            if disable_host_key_checking:
                env = dict(**os.environ)
                env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=exec_timeout,
                cwd=str(tmp_git_parent / "repo"),
                env=env,
            )

            ok = proc.returncode == 0
            return ok, {
                "success": ok,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": int(proc.returncode),
                "playbook_path": playbook_path,
                "inventory_path": inventory_path,
                "forks": forks_int,
            }
        except subprocess.TimeoutExpired:
            return False, {
                "success": False,
                "error": f"ansible-playbook timed out after {exec_timeout}s",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }
        except FileNotFoundError:
            return False, {
                "success": False,
                "error": "ansible-playbook executable not found on PATH",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }
        except GitHubClientError as exc:
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}
        except Exception as exc:  # noqa: BLE001
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}
        finally:
            if key_file:
                try:
                    Path(key_file).unlink(missing_ok=True)
                except Exception:
                    pass
            if tmp_git_parent is not None:
                shutil.rmtree(tmp_git_parent, ignore_errors=True)
