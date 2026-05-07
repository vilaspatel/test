"""
Load host inventory files from the cloned Git repository (YAML or JSON).

Hostfiles map logical names to connection targets. SSH credentials are supplied
separately as action parameters (not stored in the hostfile).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

LOG = logging.getLogger(__name__)


class HostfileError(Exception):
    """Raised when a hostfile cannot be parsed or an entry is missing or invalid."""


def load_hostfile(path: Path) -> Dict[str, Any]:
    """Load a mapping from ``path`` (``.yaml`` / ``.yml`` / ``.json``)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        elif suffix in (".yaml", ".yml"):
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
            if data is None:
                raise HostfileError("YAML hostfile is empty")
        else:
            raise HostfileError(f"unsupported hostfile extension: {suffix}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise HostfileError(f"failed to read or parse hostfile: {exc}") from exc

    if not isinstance(data, dict):
        raise HostfileError("hostfile root must be a mapping (JSON object / YAML dict)")
    return data


def resolve_host_entry(data: Dict[str, Any], host_entry: str) -> Tuple[str, Optional[int]]:
    """Resolve ``host_entry`` to ``(hostname_or_ip, optional_ssh_port)``."""
    if host_entry not in data:
        known = sorted(str(k) for k in data.keys())[:50]
        LOG.info(
            "Host entry not found in hostfile",
            extra={"host_entry": host_entry, "sample_keys": known},
        )
        raise HostfileError(f"host entry {host_entry!r} not found in hostfile")

    raw = data[host_entry]

    if isinstance(raw, str):
        host = raw.strip()
        if not host:
            raise HostfileError(f"empty host string for entry {host_entry!r}")
        return host, None

    if isinstance(raw, dict):
        host_val = (
            raw.get("host")
            or raw.get("hostname")
            or raw.get("ip")
            or raw.get("address")
        )
        if not isinstance(host_val, str) or not host_val.strip():
            raise HostfileError(
                f"entry {host_entry!r} must include host, hostname, ip, or address"
            )
        port_val = raw.get("port")
        if port_val is None:
            port_val = raw.get("ssh_port")
        port: Optional[int]
        if port_val is None:
            port = None
        else:
            try:
                port = int(port_val)
            except (TypeError, ValueError) as exc:
                raise HostfileError(f"invalid port for entry {host_entry!r}") from exc
            if port < 1 or port > 65535:
                raise HostfileError(f"port out of range for entry {host_entry!r}")
        return host_val.strip(), port

    raise HostfileError(f"entry {host_entry!r} must be a string or mapping")
