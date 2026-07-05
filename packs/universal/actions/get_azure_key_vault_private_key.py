"""
StackStorm action: retrieve SSH private key from Azure Key Vault.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from st2common.runners.base_action import Action

from lib.azure_keyvault_client import AzureKeyVaultClient, AzureKeyVaultError


class GetAzureKeyVaultPrivateKeyAction(Action):
    """Fetch private key material from Azure Key Vault."""

    def run(self, secret_name: str, secret_version: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        cfg = self.config or {}
        auth_mode = (cfg.get("azure_auth_mode") or "client_credentials").strip().lower()
        tenant_id = (cfg.get("azure_tenant_id") or "").strip()
        client_id = (cfg.get("azure_client_id") or "").strip()
        client_secret = cfg.get("azure_client_secret") or ""
        vault_url = (cfg.get("azure_key_vault_url") or "").strip()
        managed_identity_client_id = (cfg.get("azure_managed_identity_client_id") or "").strip() or None
        timeout = int(cfg.get("azure_request_timeout") or 60)

        if not vault_url:
            return False, {
                "success": False,
                "error": "Azure Key Vault URL is required in pack settings",
            }
        if auth_mode == "client_credentials":
            if not tenant_id or not client_id or not client_secret:
                return False, {
                    "success": False,
                    "error": "Azure client credentials configuration is incomplete in pack settings",
                }
        elif auth_mode != "managed_identity":
            return False, {
                "success": False,
                "error": "Unsupported azure_auth_mode. Use 'client_credentials' or 'managed_identity'",
            }

        client = AzureKeyVaultClient(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            vault_url=vault_url,
            auth_mode=auth_mode,
            managed_identity_client_id=managed_identity_client_id,
            timeout=timeout,
        )

        try:
            private_key = client.get_secret(secret_name=secret_name, secret_version=secret_version)
        except AzureKeyVaultError as exc:
            return False, {"success": False, "error": str(exc)}

        return True, {
            "success": True,
            "ssh_private_key": private_key,
        }
