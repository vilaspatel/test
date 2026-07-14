"""
StackStorm action: retrieve SSH private key (and username when present) from Delinea.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from st2common.runners.base_action import Action

from lib.delinea_client import DelineaClient, DelineaError


class GetDelineaPrivateKeyAction(Action):
    """Fetch private key material from Delinea Secret Server."""

    def run(self, secret_id: str) -> Tuple[bool, Dict[str, Any]]:
        cfg = self.config or {}
        base_url = (cfg.get("delinea_url") or "").strip()
        client_id = (cfg.get("delinea_client_id") or "").strip()
        client_secret = cfg.get("delinea_client_secret") or ""
        timeout = int(cfg.get("delinea_request_timeout") or 60)

        if not base_url or not client_id or not client_secret:
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
            return False, {"success": False, "error": str(exc)}

        private_key = payload.get("ssh_private_key")
        if not isinstance(private_key, str) or not private_key.strip():
            return False, {"success": False, "error": "Secret did not contain ssh private key value"}

        return True, {
            "success": True,
            "ssh_username": payload.get("ssh_username"),
            "ssh_private_key": private_key,
        }
