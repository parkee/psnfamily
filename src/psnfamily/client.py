"""High-level client for the PS Family ("ohana") PSN GraphQL API."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .auth import OAuthClient, PsnAuth, TokenSet
from .codec import format_pt, parse_pt, quantize_seconds
from .const import (
    APOLLO_CLIENT_NAME,
    APOLLO_CLIENT_VERSION,
    DEFAULT_TIMEOUT,
    GRAPHQL_URL,
    MIN_REQUEST_INTERVAL,
    USER_AGENT,
)
from .exceptions import (
    PsnFamilyApiError,
    PsnFamilyAuthError,
    PsnFamilyConnectionError,
    PsnFamilyScopeError,
    PsnFamilyTimeoutError,
)
from .graphql import OPERATIONS
from .hashes import OPERATION_HASHES
from .models import FamilyMember, Playtime, Presence

_LOGGER = logging.getLogger(__name__)

# GraphQL error codes that mean "authenticated but not allowed" (scope wall).
_SCOPE_CODES = {"UNAUTHORIZED", "PERMISSION_DENIED", "NOT_AUTHORIZED", "FORBIDDEN"}


class OhanaClient:
    """Client for the PlayStation Family parental-controls API.

    Wraps the PSN OAuth flow and the GraphQL gateway. Construct, then call
    :meth:`authenticate` with an npsso (first time) or pass cached tokens, then
    use the typed methods. The client serializes requests and enforces a
    minimum inter-request interval to stay within PSN rate limits.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        tokens: TokenSet | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        oauth: OAuthClient | None = None,
    ) -> None:
        """Initialize the client, optionally with a shared session/tokens.

        ``oauth`` overrides the OAuth client credentials; omit it to use the
        public PSN mobile client (note: that client's token cannot reach the
        family API — see :class:`psnfamily.OAuthClient`).
        """
        self._session = session
        self._own_session = session is None
        self._timeout = timeout
        self._auth = PsnAuth(self._ensure_sync_session(), tokens, timeout, oauth)
        self._req_lock = asyncio.Lock()
        self._last_request = 0.0

    def _ensure_sync_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    @property
    def tokens(self) -> TokenSet | None:
        """Current OAuth tokens (persist these to avoid re-entering npsso)."""
        return self._auth.tokens

    async def authenticate(self, npsso: str) -> TokenSet:
        """Authenticate with an npsso cookie. Returns the minted tokens."""
        return await self._auth.authenticate_with_npsso(npsso)

    async def close(self) -> None:
        """Close the underlying session if this client owns it."""
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> OhanaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # --- Transport ----------------------------------------------------------

    async def execute(
        self,
        operation: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a named GraphQL operation and return its ``data`` payload.

        ``operation`` must be a key in :data:`psnfamily.graphql.OPERATIONS`.
        Raises :class:`PsnFamilyScopeError` on the family-scope wall,
        :class:`PsnFamilyAuthError` on token failure, and
        :class:`PsnFamilyApiError` on other GraphQL errors.
        """
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}")
        # The PSN gateway only accepts allowlisted persisted-query hashes; it
        # rejects freeform queries. Send operationName + variables + the
        # precomputed sha256 hash, and omit the query text entirely.
        body: dict[str, Any] = {
            "operationName": operation,
            "variables": variables or {},
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": OPERATION_HASHES[operation],
                }
            },
        }
        # Try once; on 401 force a token refresh and retry a single time.
        for attempt in (1, 2):
            token = await self._auth.async_get_access_token()
            status, payload = await self._post(body, token)
            if status == 401 and attempt == 1 and self._auth.tokens:
                # Invalidate access token so the next call refreshes.
                self._auth.tokens.access_expires_at = 0.0
                continue
            return self._handle_response(operation, status, payload)
        raise PsnFamilyAuthError("PSN rejected the access token after refresh")

    async def _post(
        self, body: dict[str, Any], token: str
    ) -> tuple[int, dict[str, Any]]:
        """POST a GraphQL body, returning (status, parsed_json)."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "apollographql-client-name": APOLLO_CLIENT_NAME,
            "apollographql-client-version": APOLLO_CLIENT_VERSION,
            "User-Agent": USER_AGENT,
        }
        await self._throttle()
        session = self._ensure_sync_session()
        try:
            async with asyncio.timeout(self._timeout):
                async with session.post(
                    GRAPHQL_URL, json=body, headers=headers
                ) as resp:
                    payload = await resp.json(content_type=None)
                    return resp.status, (payload if isinstance(payload, dict) else {})
        except TimeoutError as err:
            raise PsnFamilyTimeoutError("Timeout contacting PSN GraphQL") from err
        except aiohttp.ClientError as err:
            raise PsnFamilyConnectionError(
                f"Connection error contacting PSN GraphQL: {err}"
            ) from err

    async def _throttle(self) -> None:
        """Serialize requests and enforce the minimum inter-request interval."""
        async with self._req_lock:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    def _handle_response(
        self, operation: str, status: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Map a GraphQL HTTP response to data or a typed exception."""
        if status == 403:
            raise PsnFamilyScopeError(
                f"PSN refused operation {operation!r} (HTTP 403): the account "
                "may not be the Family Manager, or this OAuth client lacks the "
                "family scope"
            )
        if status == 401:
            raise PsnFamilyAuthError(f"PSN unauthorized for {operation!r} (HTTP 401)")

        errors = payload.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            code = str(_get(first, "extensions", "code") or "").upper()
            message = first.get("message") if isinstance(first, dict) else str(errors)
            if code in _SCOPE_CODES:
                raise PsnFamilyScopeError(
                    f"PSN refused operation {operation!r}: {code} ({message})"
                )
            raise PsnFamilyApiError(
                f"GraphQL error for {operation!r}: {message}", code=code or None
            )

        if status != 200:
            raise PsnFamilyApiError(
                f"Unexpected HTTP {status} for {operation!r}: {payload}"
            )
        return payload.get("data") or {}

    # --- Typed operations ---------------------------------------------------

    async def validate_connection(self) -> list[FamilyMember]:
        """Authenticate end-to-end and confirm family-API access.

        Returns the family roster. Raises :class:`PsnFamilyScopeError` if the
        token works but the family API is walled off, or
        :class:`PsnFamilyAuthError` if the token itself is rejected. This is
        the gate an HA config flow should call.
        """
        return await self.get_family_members()

    async def get_family_members(self) -> list[FamilyMember]:
        """Return all family members (adults and children)."""
        data = await self.execute("ohanaGetFamilyMembers")
        return [
            FamilyMember.from_dict(m)
            for m in (data.get("familyMembers") or [])
            if isinstance(m, dict)
        ]

    async def get_children(self) -> list[FamilyMember]:
        """Return only the child members."""
        return [m for m in await self.get_family_members() if m.identity.is_child]

    async def get_presences(
        self, account_ids: list[str], now_playing: bool = True
    ) -> list[Presence]:
        """Return online presence (and optionally now-playing) for accounts."""
        if not account_ids:
            return []
        data = await self.execute(
            "ohanaGetChildPresences",
            {"accountIds": account_ids, "includeNowPlaying": now_playing},
        )
        return [
            Presence.from_dict(p)
            for p in (data.get("childPresences") or [])
            if isinstance(p, dict)
        ]

    async def get_playtime(self, account_id: str) -> Playtime:
        """Return a child's play-time limits, usage, and timezone."""
        data = await self.execute(
            "ohanaGetPlaytimeLimitData", {"accountId": account_id}
        )
        playtime = _get(data, "familyMember", "playtime") or {}
        return Playtime.from_dict(playtime, account_id=account_id)

    async def set_playtime_schedule(
        self, family_member_id: str, schedule: list[dict[str, Any]]
    ) -> bool:
        """Set a child's recurring weekly play-time schedule.

        ``schedule`` is a list of day settings, each a dict with:
        ``maxPlaytimeDuration`` (ISO-8601 duration, ``"P0D"`` = no limit),
        ``windowStart`` / ``windowEnd`` (playable-hours window, in minutes from
        local midnight; full day = 0..1440). The list applies across the week.
        Returns the ``success`` flag.
        """
        data = await self.execute(
            "ohanaUpdatePlaytimeSchedule",
            {"memberId": family_member_id, "schedule": schedule},
        )
        return bool(_get(data, "updatePlaytimeSchedule", "success"))

    async def set_daily_limit(
        self,
        family_member_id: str,
        seconds: int | None,
        *,
        days: int = 7,
        window: tuple[int, int] = (0, 1440),
    ) -> bool:
        """Set a uniform daily play-time limit (the same on every day).

        ``seconds=None`` or ``0`` removes the limit (``P0D``, unlimited).
        Otherwise the limit is quantized to 15 minutes. This is the working
        mechanism for "set the daily play-time" (the one-day override
        ``updateTodaysPlaytimeLimit`` is not enabled server-side for this app
        version). ``window`` is the playable-hours window in minutes.
        """
        if not seconds:
            duration = "P0D"
        else:
            duration = format_pt(quantize_seconds(max(0, seconds)))
        day = {
            "maxPlaytimeDuration": duration,
            "windowStart": window[0],
            "windowEnd": window[1],
        }
        return await self.set_playtime_schedule(family_member_id, [day] * days)

    async def set_on_limit_action(
        self, family_member_id: str, action: str
    ) -> bool:
        """Set what happens when a child reaches their play-time limit.

        ``action`` is one of :data:`psnfamily.const.ON_LIMIT_ACTIONS`
        (``"NONE"``, ``"NOTIFY_ONLY"``, ``"FORCE_LOGOUT"``). Returns the
        ``success`` flag.
        """
        data = await self.execute(
            "ohanaUpdatePlaytimeOnLimitReached",
            {"memberId": family_member_id, "action": action},
        )
        return bool(_get(data, "updatePlaytimeOnLimitReached", "success"))

    async def update_todays_playtime(
        self, family_member_id: str, change: str
    ) -> bool:
        """Apply a signed ISO-8601 duration delta to today's limit.

        ``change`` is e.g. ``"PT30M"`` to add 30 min or ``"-PT30M"`` to remove
        it (a one-day override). Returns the ``success`` flag.

        NOTE: ``updateTodaysPlaytimeLimit`` is present in the app but its
        persisted-query hash is not in the PSN gateway allowlist for app
        version 26.4.0 (it raises :class:`PsnFamilyApiError` "not whitelisted").
        Use :meth:`set_daily_limit` / :meth:`set_playtime_schedule` instead.
        """
        data = await self.execute(
            "updateTodaysPlaytimeLimit",
            {"familyMemberId": family_member_id, "playtimeDurationChange": change},
        )
        return bool(_get(data, "updateTodaysPlaytimeLimit", "success"))

    async def add_time(self, family_member_id: str, seconds: int) -> bool:
        """Add ``seconds`` of play-time to today's limit (quantized to 15 min)."""
        delta = quantize_seconds(abs(seconds))
        return await self.update_todays_playtime(family_member_id, format_pt(delta))

    async def remove_time(self, family_member_id: str, seconds: int) -> bool:
        """Remove ``seconds`` of play-time from today's limit (15-min steps)."""
        delta = quantize_seconds(abs(seconds))
        return await self.update_todays_playtime(family_member_id, format_pt(-delta))

    async def set_today_limit(
        self, member: FamilyMember, target_seconds: int
    ) -> bool:
        """Set today's effective limit to ``target_seconds`` (computes delta).

        Reads the current limit, computes the signed delta, quantizes to 15 min,
        and applies it. No-ops (returns True) if already at target.
        """
        playtime = await self.get_playtime(member.identity.account_id)
        current = playtime.today_limit_seconds or 0
        target = quantize_seconds(max(0, target_seconds))
        delta = target - current
        if delta == 0:
            return True
        return await self.update_todays_playtime(member.member_id, format_pt(delta))

    async def get_supported_parental_controls(
        self, country: str | None = None, date_of_birth: str | None = None
    ) -> dict[str, Any]:
        """Return the server-driven value domains for parental controls."""
        data = await self.execute(
            "ohanaGetSupportedParentalControls",
            {"country": country, "dateOfBirth": date_of_birth},
        )
        return data.get("supportedParentalControls") or {}

    async def update_parental_control(
        self, account_id: str, field: str, value: Any
    ) -> dict[str, Any]:
        """Update a single parental-control field.

        ``field`` is one of: ageLevel, gameContent, bluerayAgeContent,
        dvdContent, discContentCountry, spendingLimit, freeCommunication,
        internetBrowser, vrApp, contentControl, privacyRestrictionMode.
        """
        operation = _PARENTAL_OPS.get(field)
        if operation is None:
            raise ValueError(f"Unknown parental control field: {field}")
        data = await self.execute(
            operation,
            {"accountId": account_id, "parentalControls": {field: value}},
        )
        return data.get("updateParentalControls") or {}

    async def introspect(self) -> dict[str, Any]:
        """Run a standard GraphQL introspection query (for protocol research).

        Returns the raw ``__schema`` payload, or raises if introspection is
        disabled on the gateway.
        """
        body = {"query": _INTROSPECTION_QUERY, "operationName": "IntrospectionQuery"}
        token = await self._auth.async_get_access_token()
        status, payload = await self._post(body, token)
        return self._handle_response("IntrospectionQuery", status, payload)


_PARENTAL_OPS: dict[str, str] = {
    "ageLevel": "ohanaUpdateAgeLevel",
    "gameContent": "ohanaUpdateGameContent",
    "bluerayAgeContent": "ohanaUpdateBluerayAgeContent",
    "dvdContent": "ohanaUpdateDvdContent",
    "discContentCountry": "ohanaUpdateDiscContentCountry",
    "spendingLimit": "ohanaUpdateSpendingLimit",
    "freeCommunication": "ohanaUpdateFreeCommunication",
    "internetBrowser": "ohanaUpdateInternetBrowser",
    "vrApp": "ohanaUpdateVrApp",
    "contentControl": "ohanaUpdateContentControl",
    "privacyRestrictionMode": "ohanaUpdatePrivacyRestrictionMode",
}

_INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    types {
      kind name description
      fields(includeDeprecated: true) {
        name
        args { ...InputValue }
        type { ...TypeRef }
      }
      inputFields { ...InputValue }
      enumValues(includeDeprecated: true) { name }
    }
  }
}
fragment InputValue on __InputValue {
  name type { ...TypeRef } defaultValue
}
fragment TypeRef on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name } } }
}
"""


def _get(d: Any, *keys: str) -> Any:
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur
