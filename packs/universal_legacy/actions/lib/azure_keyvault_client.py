"""
Azure Key Vault REST client supporting Azure AD client credentials or Managed Identity.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

LOG = logging.getLogger(__name__)


class AzureKeyVaultError(Exception):
    """Raised when Key Vault authentication or secret retrieval fails."""


class AzureKeyVaultClient:
    """Minimal REST client for Key Vault token + secret retrieval."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        vault_url: str,
        auth_mode: str = "client_credentials",
        managed_identity_client_id: Optional[str] = None,
        timeout: int = 60,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.tenant_id = tenant_id.strip()
        self.client_id = client_id.strip()
        self.client_secret = client_secret
        self.vault_url = vault_url.rstrip("/")
        self.auth_mode = (auth_mode or "client_credentials").strip().lower()
        self.managed_identity_client_id = (managed_identity_client_id or "").strip() or None
        self.timeout = timeout
        self._session = session or requests.Session()
        self._access_token: Optional[str] = None

    def authenticate(self) -> str:
        """Obtain an access token using configured auth mode."""
        if self.auth_mode == "managed_identity":
            return self._authenticate_managed_identity()
        if self.auth_mode != "client_credentials":
            raise AzureKeyVaultError(
                "Unsupported azure_auth_mode. Use 'client_credentials' or 'managed_identity'"
            )
        return self._authenticate_client_credentials()

    def _authenticate_client_credentials(self) -> str:
        """Obtain an Azure AD access token using app registration credentials."""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://vault.azure.net/.default",
        }
        try:
            resp = self._session.post(token_url, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AzureKeyVaultError(f"Azure AD token request failed: {exc}") from exc

        if not resp.ok:
            raise AzureKeyVaultError(
                f"Azure AD token HTTP {resp.status_code}: {self._safe_error_snippet(resp)}"
            )

        payload = resp.json()
        token = payload.get("access_token")
        if not token or not isinstance(token, str):
            raise AzureKeyVaultError("Azure AD token response missing access_token")
        self._access_token = token
        return token

    def _authenticate_managed_identity(self) -> str:
        """Obtain token from IMDS endpoint for VM/VMSS assigned Managed Identity."""
        params = {
            "api-version": "2018-02-01",
            "resource": "https://vault.azure.net",
        }
        if self.managed_identity_client_id:
            params["client_id"] = self.managed_identity_client_id

        headers = {"Metadata": "true"}
        token_url = "http://169.254.169.254/metadata/identity/oauth2/token"

        try:
            resp = self._session.get(token_url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AzureKeyVaultError(f"Managed Identity token request failed: {exc}") from exc

        if not resp.ok:
            raise AzureKeyVaultError(
                f"Managed Identity token HTTP {resp.status_code}: {self._safe_error_snippet(resp)}"
            )

        payload = resp.json()
        token = payload.get("access_token")
        if not token or not isinstance(token, str):
            raise AzureKeyVaultError("Managed Identity token response missing access_token")
        self._access_token = token
        return token

    def get_secret(self, secret_name: str, secret_version: Optional[str] = None) -> str:
        """Retrieve a Key Vault secret value by name and optional version."""
        if self._access_token is None:
            self.authenticate()

        clean_name = secret_name.strip()
        if not clean_name:
            raise AzureKeyVaultError("secret_name is required")

        if secret_version:
            secret_url = f"{self.vault_url}/secrets/{clean_name}/{secret_version}?api-version=7.4"
        else:
            secret_url = f"{self.vault_url}/secrets/{clean_name}?api-version=7.4"

        headers = {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

        try:
            resp = self._session.get(secret_url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AzureKeyVaultError(f"Key Vault secret GET failed: {exc}") from exc

        if resp.status_code == 401:
            self._access_token = None
            self.authenticate()
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                resp = self._session.get(secret_url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                raise AzureKeyVaultError(f"Key Vault secret GET retry failed: {exc}") from exc

        if not resp.ok:
            raise AzureKeyVaultError(
                f"Key Vault secret HTTP {resp.status_code}: {self._safe_error_snippet(resp)}"
            )

        payload = resp.json()
        value = payload.get("value")
        if not isinstance(value, str) or not value:
            raise AzureKeyVaultError("Key Vault secret response missing value")
        return value

    @staticmethod
    def _safe_error_snippet(resp: requests.Response, limit: int = 200) -> str:
        text = (resp.text or "").replace("\n", " ").strip()
        if len(text) > limit:
            return text[:limit] + "..."
        return text
