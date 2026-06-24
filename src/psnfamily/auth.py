"""PSN OAuth authentication (npsso -> code -> token -> refresh).

Implements the Sony "Versa" authorization-code flow exactly as the PS Family
app and the wider PSN ecosystem (psn-api, PSNAWP) use it, but headless: the
user supplies an ``npsso`` cookie obtained from a browser session, which the
library exchanges for an access token (~1 h) and a refresh token (~60 days).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import (
    AUTHORIZE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    DEFAULT_TIMEOUT,
    REDIRECT_URI,
    SCOPE,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_URL,
)
from .exceptions import (
    PsnFamilyAuthError,
    PsnFamilyConnectionError,
    PsnFamilyTimeoutError,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OAuthClient:
    """OAuth client configuration for the PSN authorization flow.

    Defaults to the public PSN mobile ("PS App") client, which authenticates
    fine but whose token lacks the *family* scope the ``ohana*`` operations
    require. To reach the family API, supply the PS Family app's own client
    credentials (client_id/client_secret/scope/redirect_uri).
    """

    client_id: str = CLIENT_ID
    client_secret: str = CLIENT_SECRET
    redirect_uri: str = REDIRECT_URI
    scope: str = SCOPE

    @property
    def basic_auth(self) -> str:
        """The HTTP Basic ``Authorization`` header value for token requests."""
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()


@dataclass(slots=True)
class TokenSet:
    """OAuth tokens plus absolute expiry timestamps (epoch seconds)."""

    access_token: str
    refresh_token: str
    access_expires_at: float
    refresh_expires_at: float

    @property
    def access_valid(self) -> bool:
        """True if the access token is still usable (with safety margin)."""
        return bool(self.access_token) and (
            time.time() < self.access_expires_at - TOKEN_EXPIRY_MARGIN
        )

    @property
    def refresh_valid(self) -> bool:
        """True if the refresh token can still mint new access tokens."""
        return bool(self.refresh_token) and time.time() < self.refresh_expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence (e.g. an HA config entry)."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
            "refresh_expires_at": self.refresh_expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        """Restore from :meth:`to_dict`."""
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            access_expires_at=float(data["access_expires_at"]),
            refresh_expires_at=float(data["refresh_expires_at"]),
        )


class PsnAuth:
    """Manages the PSN OAuth token lifecycle for a single account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: TokenSet | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        oauth: OAuthClient | None = None,
    ) -> None:
        """Initialize with an aiohttp session and optional cached tokens."""
        self._session = session
        self._tokens = tokens
        self._timeout = timeout
        self._oauth = oauth or OAuthClient()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> TokenSet | None:
        """The current token set, if authenticated."""
        return self._tokens

    async def authenticate_with_npsso(self, npsso: str) -> TokenSet:
        """Run the full npsso -> code -> token exchange.

        Raises :class:`PsnFamilyAuthError` if the npsso is invalid/expired.
        """
        code = await self._fetch_authorization_code(npsso)
        tokens = await self._exchange_code(code)
        self._tokens = tokens
        return tokens

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing if necessary.

        Single-flight: concurrent callers share one refresh. Raises
        :class:`PsnFamilyAuthError` if no valid tokens and refresh is not
        possible (caller must re-authenticate with a fresh npsso).
        """
        if self._tokens and self._tokens.access_valid:
            return self._tokens.access_token
        async with self._lock:
            # Re-check inside the lock — another caller may have refreshed.
            if self._tokens and self._tokens.access_valid:
                return self._tokens.access_token
            if not self._tokens or not self._tokens.refresh_valid:
                raise PsnFamilyAuthError(
                    "No valid PSN token; a fresh npsso is required"
                )
            self._tokens = await self._refresh(self._tokens.refresh_token)
            return self._tokens.access_token

    # --- Internal flow steps ------------------------------------------------

    async def _fetch_authorization_code(self, npsso: str) -> str:
        """Step 1: exchange the npsso cookie for an authorization code."""
        params = {
            "access_type": "offline",
            "client_id": self._oauth.client_id,
            "redirect_uri": self._oauth.redirect_uri,
            "response_type": "code",
            "scope": self._oauth.scope,
        }
        headers = {"Cookie": f"npsso={npsso}"}
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.get(
                    AUTHORIZE_URL,
                    params=params,
                    headers=headers,
                    allow_redirects=False,
                ) as resp:
                    location = resp.headers.get("Location", "")
                    await resp.read()
        except TimeoutError as err:
            raise PsnFamilyTimeoutError("Timeout contacting PSN authorize") from err
        except aiohttp.ClientError as err:
            raise PsnFamilyConnectionError(
                f"Connection error contacting PSN authorize: {err}"
            ) from err

        query = parse_qs(urlparse(location).query)
        codes = query.get("code")
        if not codes:
            raise PsnFamilyAuthError(
                "PSN did not return an authorization code; "
                "the npsso is invalid or expired"
            )
        return codes[0]

    async def _exchange_code(self, code: str) -> TokenSet:
        """Step 2: exchange the authorization code for tokens."""
        data = {
            "code": code,
            "redirect_uri": self._oauth.redirect_uri,
            "grant_type": "authorization_code",
            "token_format": "jwt",
        }
        return await self._token_request(data)

    async def _refresh(self, refresh_token: str) -> TokenSet:
        """Step 3: mint a new access token from the refresh token."""
        data = {
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "token_format": "jwt",
            "scope": self._oauth.scope,
        }
        return await self._token_request(data)

    async def _token_request(self, data: dict[str, str]) -> TokenSet:
        """POST to the token endpoint and parse the token response."""
        headers = {
            "Authorization": self._oauth.basic_auth,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.post(
                    TOKEN_URL, data=data, headers=headers
                ) as resp:
                    payload = await resp.json(content_type=None)
                    status = resp.status
        except TimeoutError as err:
            raise PsnFamilyTimeoutError("Timeout contacting PSN token") from err
        except aiohttp.ClientError as err:
            raise PsnFamilyConnectionError(
                f"Connection error contacting PSN token: {err}"
            ) from err

        if status != 200 or "access_token" not in payload:
            error = payload.get("error") if isinstance(payload, dict) else None
            raise PsnFamilyAuthError(
                f"PSN token request failed (HTTP {status}): {error or payload}"
            )

        now = time.time()
        return TokenSet(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            access_expires_at=now + float(payload.get("expires_in", 3600)),
            refresh_expires_at=now
            + float(payload.get("refresh_token_expires_in", 5184000)),
        )
