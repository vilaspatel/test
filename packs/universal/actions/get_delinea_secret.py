"""
StackStorm action: retrieve SSH credentials from Delinea Secret Server.

Returned fields may contain sensitive values; corresponding YAML marks them as secret.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from st2common.runners.base_action import Action

from lib.delinea_client import DelineaClient, DelineaError

LOG = logging.getLogger(__name__)


class GetDelineaSecretAction(Action):
    """Fetch and normalize SSH credential fields for a given secret identifier."""

    def run(self, secret_id: str) -> Tuple[bool, Dict[str, Any]]:
        cfg = self.config or {}
        base_url = (cfg.get("delinea_url") or "").strip()
        client_id = (cfg.get("delinea_client_id") or "").strip()
        client_secret = cfg.get("delinea_client_secret") or ""
        timeout = int(cfg.get("delinea_request_timeout") or 60)

        if not base_url or not client_id or not client_secret:
            LOG.error("Delinea configuration is incomplete (missing url, client_id, or client_secret)")
            return False, {
                "success": False,
                "error": "Delinea configuration is incomplete in pack settings",
            }

        client = DelineaClient(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            timeout=timeout,
        )

        try:
            payload = client.get_secret(secret_id)
        except DelineaError as exc:
            LOG.warning(
                "Failed to retrieve Delinea secret",
                extra={"secret_id": str(secret_id), "error_type": type(exc).__name__},
            )
            return False, {"success": False, "error": str(exc)}

        username = payload.get("ssh_username")
        if not username:
            LOG.warning(
                "Secret missing SSH username field",
                extra={"secret_id": str(secret_id)},
            )
            return False, {"success": False, "error": "Secret did not contain a recognizable username field"}

        password = payload.get("ssh_password")
        private_key = payload.get("ssh_private_key")

        if password is None and private_key is None:
            LOG.warning(
                "Secret missing password and private key",
                extra={"secret_id": str(secret_id)},
            )
            return False, {"success": False, "error": "Secret did not contain password or private key"}

        result: Dict[str, Any] = {
            "success": True,
            "ssh_username": username,
            "ssh_password": password,
            "ssh_private_key": private_key,
        }
        return True, result
