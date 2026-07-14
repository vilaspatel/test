"""
Delinea Secret Server (Thycotic) REST API client using OAuth2 client credentials.

This module avoids logging secret values. Token strings are never written to logs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

LOG = logging.getLogger(__name__)


class DelineaError(Exception):
    """Raised when Delinea Secret Server API calls fail or return unexpected data."""


class DelineaClient:
    """
    Minimal REST client for Secret Server Cloud / on-prem OAuth2 and secret retrieval.

    Endpoints follow common Thycotic Secret Server REST patterns:
    - POST /oauth2/token (client_credentials)
    - GET /api/v1/secrets/{secretId}
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: int = 60,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._session = session or requests.Session()
        self._access_token: Optional[str] = None

    def authenticate(self) -> str:
        """
        Obtain an OAuth2 access token using the client credentials grant.

        Returns:
            Bearer token string (not logged).

        Raises:
            DelineaError: On HTTP errors or missing access_token in response.
        """
        token_url = f"{self.base_url}/oauth2/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        LOG.info(
            "Requesting Delinea OAuth token",
            extra={"delinea_token_url": token_url, "client_id_prefix": self._mask_id(self.client_id)},
        )
        try:
            resp = self._session.post(
                token_url,
                data=data,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DelineaError(f"Delinea token request failed: {exc}") from exc

        if not resp.ok:
            LOG.warning(
                "Delinea token request failed",
                extra={
                    "status_code": resp.status_code,
                    "content_type": resp.headers.get("Content-Type", ""),
                },
            )
            raise DelineaError(
                f"Delinea token HTTP {resp.status_code}: {self._safe_error_snippet(resp)}"
            )

        payload = resp.json()
        token = payload.get("access_token")
        if not token or not isinstance(token, str):
            raise DelineaError("Delinea token response missing access_token")
        self._access_token = token
        return token

    def get_secret(self, secret_id: str) -> Dict[str, Any]:
        """
        Retrieve secret metadata and fields by numeric or string secret identifier.

        Args:
            secret_id: Secret Server secret ID.

        Returns:
            Dict with raw API payload under 'raw' and normalized SSH fields:
            ssh_username, ssh_password, ssh_private_key (optional strings).

        Raises:
            DelineaError: On failure to retrieve or parse.
        """
        if self._access_token is None:
            self.authenticate()

        url = f"{self.base_url}/api/v1/secrets/{secret_id}"
        headers = {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}
        LOG.info(
            "Fetching Delinea secret",
            extra={"secret_id": str(secret_id), "request_url": url},
        )
        try:
            resp = self._session.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DelineaError(f"Delinea secret GET failed: {exc}") from exc

        if resp.status_code == 401:
            # Token may have expired; retry once after re-auth.
            LOG.info("Delinea secret GET returned 401; re-authenticating")
            self._access_token = None
            self.authenticate()
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                resp = self._session.get(url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                raise DelineaError(f"Delinea secret GET retry failed: {exc}") from exc

        if not resp.ok:
            LOG.warning(
                "Delinea secret GET failed",
                extra={"status_code": resp.status_code, "secret_id": str(secret_id)},
            )
            raise DelineaError(
                f"Delinea secret HTTP {resp.status_code}: {self._safe_error_snippet(resp)}"
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise DelineaError("Delinea secret response was not valid JSON") from exc

        normalized = self._normalize_ssh_credentials(body)
        return {"raw": body, **normalized}

    @staticmethod
    def _mask_id(client_id: str) -> str:
        if not client_id:
            return ""
        if len(client_id) <= 6:
            return "***"
        return f"{client_id[:4]}...{client_id[-2:]}"

    @staticmethod
    def _safe_error_snippet(resp: requests.Response, limit: int = 200) -> str:
        text = (resp.text or "").replace("\n", " ").strip()
        if len(text) > limit:
            return text[:limit] + "..."
        return text

    def _normalize_ssh_credentials(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map Secret Server fields to ssh_* keys used by actions.

        Supports common field names on items[] as well as top-level shortcuts.
        """
        username = (
            body.get("UserName")
            or body.get("username")
            or self._find_item_value(body, {"Username", "User", "Login", "user"})
        )
        password = self._find_item_value(body, {"Password", "password", "Pass"})
        private_key = self._find_item_value(
            body,
            {"Private Key", "PrivateKey", "Key", "SSH Private Key", "Private"},
        )

        return {
            "ssh_username": username.strip() if isinstance(username, str) else username,
            "ssh_password": password if isinstance(password, str) or password is None else str(password),
            "ssh_private_key": private_key if isinstance(private_key, str) or private_key is None else str(private_key),
        }

    def _find_item_value(self, body: Dict[str, Any], names: set) -> Optional[str]:
        items = body.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                field_name = item.get("fieldName") or item.get("FieldName") or item.get("name")
                if isinstance(field_name, str) and field_name.strip() in names:
                    val = item.get("itemValue") or item.get("ItemValue") or item.get("value")
                    if isinstance(val, str):
                        return val
        return None
