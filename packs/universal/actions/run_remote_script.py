"""
Clone a GitHub repo, validate a bash script path, upload over SSH, and execute remotely.

Security: credentials are never written to logs. Path traversal and unsafe extensions
are rejected. Optional allowlist is enforced when configured.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from st2common.runners.base_action import Action

from lib.delinea_client import DelineaClient, DelineaError
from lib.github_client import GitHubClient, GitHubClientError
from lib.hostfile import HostfileError, load_hostfile, resolve_host_entry
from lib.ssh_client import SSHClient, SSHClientError

LOG = logging.getLogger(__name__)

# Default allowlist merged with pack config `allowed_scripts`. Empty means "only path rules".
ALLOWED_SCRIPTS: List[str] = []

HOSTFILE_SUFFIXES: FrozenSet[str] = frozenset({".yaml", ".yml", ".json"})


def _coerce_bool(value: Any, default: bool) -> bool:
    """Parse booleans from pack config / StackStorm (bool, int, or string)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on", "y"):
        return True
    if s in ("false", "0", "no", "off", "n", ""):
        return False
    return default


def _validate_script_path(script_path: str, extra_allowlist: Optional[List[str]]) -> None:
    """
    Validate repository-relative script path for traversal and extension rules.

    Raises:
        ValueError: When validation fails.
    """
    if not script_path or not str(script_path).strip():
        raise ValueError("script_path is required")
    raw = str(script_path).strip()
    posix = PurePosixPath(raw)
    if posix.is_absolute():
        raise ValueError("absolute script paths are not allowed")
    if ".." in posix.parts:
        raise ValueError("path traversal sequences are not allowed (../)")
    if not raw.endswith(".sh"):
        raise ValueError("only .sh scripts are permitted")

    allow = list(ALLOWED_SCRIPTS)
    if extra_allowlist:
        allow.extend(extra_allowlist)
    allow = [a for a in allow if a]
    if allow:
        basename = posix.name
        if basename not in allow and raw not in allow:
            raise ValueError("script is not in the configured allowlist")


def _validate_repo_relative_path(param_name: str, raw_path: str, allowed_suffixes: FrozenSet[str]) -> None:
    """Validate a repository-relative path (no traversal); optionally enforce file suffixes."""
    if not raw_path or not str(raw_path).strip():
        raise ValueError(f"{param_name} is required")
    raw = str(raw_path).strip()
    posix = PurePosixPath(raw)
    if posix.is_absolute():
        raise ValueError(f"{param_name} must not be an absolute path")
    if ".." in posix.parts:
        raise ValueError(f"{param_name} must not contain path traversal (../)")
    suffix = posix.suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"{param_name} must use one of these extensions: {sorted(allowed_suffixes)}")


class RunRemoteScriptAction(Action):
    """End-to-end GitHub clone, validation, SFTP upload, and remote execution."""

    def run(
        self,
        github_repo: str,
        github_branch: str,
        script_path: str,
        target_host: Optional[str] = None,
        hostfile_path: Optional[str] = None,
        host_entry: Optional[str] = None,
        script_args: Optional[List[Any]] = None,
        timeout: Optional[int] = None,
        delinea_secret_id: Optional[str] = None,
        ssh_username: Optional[str] = None,
        ssh_password: Optional[str] = None,
        ssh_private_key: Optional[str] = None,
        ssh_port: Optional[int] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        cfg = self.config or {}
        github_token = (cfg.get("github_token") or "").strip() or None
        cfg_ssh_username = (cfg.get("ssh_username") or "").strip() or None
        cfg_ssh_private_key = cfg.get("ssh_private_key") or None
        clone_timeout = int(cfg.get("git_clone_timeout") or 600)
        default_port = int(cfg.get("ssh_port") or 22)
        delinea_timeout = int(cfg.get("delinea_request_timeout") or 60)
        extra_allow = cfg.get("allowed_scripts") or []

        exec_timeout = int(timeout) if timeout is not None else 3600
        normalized_script_args: List[str] = []
        if script_args is not None:
            if not isinstance(script_args, list):
                return False, {
                    "success": False,
                    "error": "script_args must be an array/list of values",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": -1,
                }
            normalized_script_args = [str(v) for v in script_args]

        explicit_ssh_port: Optional[int] = int(ssh_port) if ssh_port is not None else None

        hf_path_stripped = (hostfile_path or "").strip() or None
        hf_entry_stripped = (host_entry or "").strip() or None
        target_stripped = (target_host or "").strip() or None
        use_hostfile = bool(hf_path_stripped and hf_entry_stripped)

        try:
            _validate_script_path(script_path, extra_allow if isinstance(extra_allow, list) else [])
            if bool(hf_path_stripped) ^ bool(hf_entry_stripped):
                raise ValueError("hostfile_path and host_entry must both be set, or both omitted")
            if use_hostfile:
                _validate_repo_relative_path("hostfile_path", hf_path_stripped or "", HOSTFILE_SUFFIXES)
            if not use_hostfile and not target_stripped:
                raise ValueError("provide target_host or both hostfile_path and host_entry")
            if use_hostfile and target_stripped:
                LOG.info(
                    "Both hostfile and target_host provided; using hostfile entry",
                    extra={"host_entry": hf_entry_stripped},
                )
        except ValueError as exc:
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}

        username: Optional[str] = ssh_username
        password: Optional[str] = ssh_password
        private_key: Optional[str] = ssh_private_key

        try:
            if username and (password or private_key):
                LOG.info("Using caller-supplied SSH credentials (not logged)")
            elif delinea_secret_id:
                username, password, private_key = self._fetch_from_delinea(delinea_secret_id, delinea_timeout)
            else:
                # Final fallback from pack config to avoid passing key each execution.
                username = username or cfg_ssh_username
                private_key = private_key or cfg_ssh_private_key
        except DelineaError as exc:
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}

        if not username:
            return False, {"success": False, "error": "SSH username is missing", "stdout": "", "stderr": "", "exit_code": -1}
        if not password and not private_key:
            return False, {
                "success": False,
                "error": "Neither SSH password nor private key available (input, Delinea, or pack config)",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }

        # Normalize empty strings to None for Paramiko
        if password == "":
            password = None
        if private_key == "":
            private_key = None

        tmp_git_parent: Optional[Path] = None
        ssh: Optional[SSHClient] = None
        try:
            gh = GitHubClient(github_token=github_token, clone_timeout=clone_timeout)
            tmp_git_parent = gh.clone_repo(github_repo.strip(), github_branch.strip())
            local_script = gh.get_script_path(tmp_git_parent, script_path.strip())

            if use_hostfile:
                hostfile_file = gh.get_repo_file(tmp_git_parent, hf_path_stripped, HOSTFILE_SUFFIXES)
                inventory = load_hostfile(hostfile_file)
                resolved_host, port_from_file = resolve_host_entry(inventory, hf_entry_stripped)
                if explicit_ssh_port is not None:
                    port = explicit_ssh_port
                elif port_from_file is not None:
                    port = port_from_file
                else:
                    port = default_port
                LOG.info(
                    "Resolved target from hostfile",
                    extra={
                        "host_entry": hf_entry_stripped,
                        "resolved_host": resolved_host,
                        "ssh_port": port,
                        "hostfile_path": hf_path_stripped,
                    },
                )
            else:
                resolved_host = target_stripped or ""
                port = explicit_ssh_port if explicit_ssh_port is not None else default_port

            remote_path = f"/tmp/st2-script-{int(time.time())}.sh"
            quoted_remote = shlex.quote(remote_path)

            known_hosts = (cfg.get("ssh_known_hosts_path") or "").strip() or None
            # Default: accept unknown keys once and persist (no pack config required).
            # ssh_strict_host_key_checking true => RejectPolicy only (must pre-seed known_hosts).
            strict_hk = _coerce_bool(cfg.get("ssh_strict_host_key_checking"), False)
            auto_add_hk = not strict_hk

            save_path_auto = (
                os.path.expanduser(known_hosts)
                if known_hosts
                else os.path.expanduser("~/.ssh/known_hosts")
            )

            LOG.info(
                "SSH host key settings resolved",
                extra={
                    "auto_add_host_key": auto_add_hk,
                    "strict_host_key_checking": strict_hk,
                    "known_hosts_save_path": save_path_auto if auto_add_hk else None,
                },
            )

            ssh = SSHClient(
                hostname=resolved_host,
                port=port,
                username=username,
                password=password,
                private_key_pem=private_key,
                command_timeout=exec_timeout,
                known_hosts_path=known_hosts,
                strict_host_key_checking=strict_hk,
                auto_add_host_key=auto_add_hk,
                known_hosts_save_path=save_path_auto if auto_add_hk else None,
            )
            ssh.connect()
            ssh.upload_file(local_script, remote_path)

            chmod_cmd = f"chmod +x {quoted_remote}"
            chmod_res = ssh.execute(chmod_cmd)
            if chmod_res.exit_code != 0:
                return False, {
                    "success": False,
                    "stdout": chmod_res.stdout,
                    "stderr": chmod_res.stderr,
                    "exit_code": chmod_res.exit_code,
                    "error": "chmod +x failed on remote host",
                    "remote_script_path": remote_path,
                    "resolved_host": resolved_host,
                    "ssh_port": port,
                    "host_entry": hf_entry_stripped if use_hostfile else None,
                    "hostfile_path": hf_path_stripped if use_hostfile else None,
                }

            arg_tail = " ".join(shlex.quote(a) for a in normalized_script_args)
            run_cmd = f"/bin/bash {quoted_remote}"
            if arg_tail:
                run_cmd = f"{run_cmd} {arg_tail}"
            run_res = ssh.execute(run_cmd)
            ok = run_res.exit_code == 0
            body = {
                "success": ok,
                "stdout": run_res.stdout,
                "stderr": run_res.stderr,
                "exit_code": run_res.exit_code,
                "remote_script_path": remote_path,
                "resolved_host": resolved_host,
                "ssh_port": port,
                "host_entry": hf_entry_stripped if use_hostfile else None,
                "hostfile_path": hf_path_stripped if use_hostfile else None,
            }
            return ok, body
        except HostfileError as exc:
            LOG.warning("Hostfile resolution failed", extra={"error_type": type(exc).__name__})
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}
        except GitHubClientError as exc:
            LOG.warning("GitHub clone failed", extra={"error_type": type(exc).__name__})
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}
        except SSHClientError as exc:
            LOG.warning("SSH operation failed", extra={"error_type": type(exc).__name__})
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}
        except Exception as exc:  # noqa: BLE001 - surface bounded error string only
            LOG.exception("Unexpected failure during remote execution")
            return False, {"success": False, "error": str(exc), "stdout": "", "stderr": "", "exit_code": -1}
        finally:
            if ssh is not None:
                ssh.close()
            if tmp_git_parent is not None:
                shutil.rmtree(tmp_git_parent, ignore_errors=True)

    def _fetch_from_delinea(self, secret_id: str, http_timeout: int) -> Tuple[str, Optional[str], Optional[str]]:
        cfg = self.config or {}
        base_url = (cfg.get("delinea_url") or "").strip()
        client_id = (cfg.get("delinea_client_id") or "").strip()
        client_secret = cfg.get("delinea_client_secret") or ""
        if not base_url or not client_id or not client_secret:
            raise DelineaError("Delinea configuration is incomplete in pack settings")

        client = DelineaClient(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            timeout=http_timeout,
        )
        secret_payload = client.get_secret(secret_id)
        username = secret_payload.get("ssh_username")
        if not username:
            raise DelineaError("Secret did not contain a recognizable username field")
        password = secret_payload.get("ssh_password")
        private_key = secret_payload.get("ssh_private_key")
        if password is None and private_key is None:
            raise DelineaError("Secret did not contain password or private key")
        return str(username), password, private_key
