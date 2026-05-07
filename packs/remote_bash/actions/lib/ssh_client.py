"""
SSH/SFTP helper built on Paramiko with explicit timeouts and no credential logging.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Optional

import paramiko

LOG = logging.getLogger(__name__)


class SSHClientError(Exception):
    """Raised for SSH connection, SFTP, or command execution failures."""


@dataclass
class SSHExecResult:
    """Structured result from a remote command."""

    stdout: str
    stderr: str
    exit_code: int


class SSHClient:
    """Password or key-based SSH with upload and execute helpers."""

    def __init__(
        self,
        hostname: str,
        port: int = 22,
        username: Optional[str] = None,
        password: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        command_timeout: int = 3600,
        banner_timeout: int = 30,
        auth_timeout: int = 30,
        known_hosts_path: Optional[str] = None,
        strict_host_key_checking: bool = True,
        auto_add_host_key: bool = False,
        known_hosts_save_path: Optional[str] = None,
    ) -> None:
        self.hostname = hostname
        self.port = port
        self.username = username or ""
        self.password = password
        self.private_key_pem = private_key_pem
        self.command_timeout = command_timeout
        self.banner_timeout = banner_timeout
        self.auth_timeout = auth_timeout
        self.known_hosts_path = known_hosts_path
        self.strict_host_key_checking = strict_host_key_checking
        self.auto_add_host_key = auto_add_host_key
        self.known_hosts_save_path = known_hosts_save_path
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        """Open SSH session using password or private key."""
        self.close()
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except Exception:
            LOG.warning("Unable to load system host keys; SSH host verification may fail")

        if self.known_hosts_path:
            extra = os.path.expanduser(self.known_hosts_path)
            if os.path.isfile(extra):
                try:
                    client.load_host_keys(extra)
                    LOG.info("Loaded additional SSH known_hosts", extra={"path": extra})
                except Exception as exc:
                    LOG.warning("Failed to load ssh_known_hosts_path", extra={"path": extra, "error": str(exc)})
            else:
                LOG.warning("ssh_known_hosts_path does not exist", extra={"path": extra})

        if self.auto_add_host_key:
            LOG.info(
                "SSH auto-add host key enabled; new keys will be saved after connect",
                extra={"save_path": self.known_hosts_save_path or "~/.ssh/known_hosts"},
            )
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        elif self.strict_host_key_checking:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            LOG.warning(
                "SSH strict host key checking is disabled (testing/lab only; MITM risk)"
            )
            client.set_missing_host_key_policy(paramiko.WarningPolicy())
        pkey = None
        if self.private_key_pem:
            try:
                pkey = paramiko.RSAKey.from_private_key(StringIO(self.private_key_pem))
            except paramiko.SSHException:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(StringIO(self.private_key_pem))
                except paramiko.SSHException as exc:
                    try:
                        pkey = paramiko.ECDSAKey.from_private_key(StringIO(self.private_key_pem))
                    except paramiko.SSHException as exc2:
                        raise SSHClientError("Unable to parse SSH private key") from exc2

        LOG.info(
            "Opening SSH connection",
            extra={
                "host": self.hostname,
                "port": self.port,
                "username": self.username,
                "auth": "key" if pkey is not None else "password",
            },
        )
        try:
            client.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password if pkey is None else None,
                pkey=pkey,
                look_for_keys=False,
                allow_agent=False,
                timeout=self.banner_timeout,
                banner_timeout=self.banner_timeout,
                auth_timeout=self.auth_timeout,
            )
        except (paramiko.SSHException, socket.error, EOFError) as exc:
            raise SSHClientError(f"SSH connect failed: {exc}") from exc

        if self.auto_add_host_key:
            save_path = self.known_hosts_save_path or os.path.expanduser("~/.ssh/known_hosts")
            save_dir = os.path.dirname(save_path)
            try:
                if save_dir and not os.path.isdir(save_dir):
                    os.makedirs(save_dir, mode=0o700, exist_ok=True)
                client.save_host_keys(save_path)
                LOG.info("Persisted SSH host keys after connect", extra={"path": save_path})
            except OSError as exc:
                LOG.warning(
                    "Could not persist SSH host keys to disk",
                    extra={"path": save_path, "error": str(exc)},
                )

        self._client = client

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """Upload a local file via SFTP atomically to ``remote_path``."""
        if self._client is None:
            raise SSHClientError("SSH client is not connected")
        if not local_path.is_file():
            raise SSHClientError("local upload path is not a file")

        LOG.info(
            "Starting SFTP upload",
            extra={
                "remote_path": remote_path,
                "local_size": local_path.stat().st_size,
            },
        )
        try:
            sftp = self._client.open_sftp()
        except Exception as exc:
            raise SSHClientError(f"SFTP initialization failed: {exc}") from exc

        remote_tmp = f"{remote_path}.partial"
        try:
            sftp.put(str(local_path), remote_tmp)
            try:
                sftp.rename(remote_tmp, remote_path)
            except OSError:
                try:
                    sftp.remove(remote_path)
                except OSError:
                    pass
                sftp.rename(remote_tmp, remote_path)
        except Exception as exc:
            try:
                sftp.remove(remote_tmp)
            except Exception:
                pass
            raise SSHClientError(f"SFTP upload failed: {exc}") from exc
        finally:
            try:
                sftp.close()
            except Exception:
                pass

    def execute(self, command: str) -> SSHExecResult:
        """Run a remote command; capture stdout, stderr, exit code."""
        if self._client is None:
            raise SSHClientError("SSH client is not connected")

        LOG.info("Executing remote command", extra={"command_preview": command[:120]})
        try:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=self.command_timeout)
        except Exception as exc:
            raise SSHClientError(f"exec_command failed: {exc}") from exc

        try:
            out_chunks = stdout.readlines()
            err_chunks = stderr.readlines()
            exit_code = stdout.channel.recv_exit_status()
        except socket.timeout as exc:
            raise SSHClientError("remote command timed out") from exc
        finally:
            try:
                stdin.close()
            except Exception:
                pass

        return SSHExecResult(
            stdout="".join(out_chunks),
            stderr="".join(err_chunks),
            exit_code=int(exit_code),
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
