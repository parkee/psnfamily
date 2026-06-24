"""Tests for auth token lifecycle and GraphQL client error handling.

Uses a programmable fake aiohttp session (no network).
"""

import time

import pytest

from psnfamily.auth import PsnAuth, TokenSet
from psnfamily.client import OhanaClient
from psnfamily.exceptions import (
    PsnFamilyApiError,
    PsnFamilyAuthError,
    PsnFamilyScopeError,
)


class FakeResp:
    def __init__(self, *, status=200, headers=None, json_data=None):
        self.status = status
        self.headers = headers or {}
        self._json = json_data or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return b""

    async def json(self, content_type=None):
        return self._json


class FakeSession:
    """Queues responses per (method) and records requests."""

    def __init__(self):
        self.closed = False
        self.get_queue: list[FakeResp] = []
        self.post_queue: list[FakeResp] = []
        self.requests: list[tuple] = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.get_queue.pop(0)

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self.post_queue.pop(0)

    async def close(self):
        self.closed = True


def _token_json():
    return {
        "access_token": "access-abc",
        "refresh_token": "refresh-xyz",
        "expires_in": 3600,
        "refresh_token_expires_in": 5184000,
        "scope": "psn:mobile.v2.core psn:clientapp",
    }


# --- TokenSet logic ---------------------------------------------------------


def test_tokenset_validity_and_roundtrip():
    now = time.time()
    ts = TokenSet("a", "r", now + 3600, now + 99999)
    assert ts.access_valid and ts.refresh_valid
    expired = TokenSet("a", "r", now - 10, now + 99999)
    assert not expired.access_valid and expired.refresh_valid
    assert TokenSet.from_dict(ts.to_dict()).access_token == "a"


# --- Auth flow --------------------------------------------------------------


async def test_authenticate_with_npsso():
    session = FakeSession()
    session.get_queue.append(
        FakeResp(
            status=302,
            headers={"Location": "com.scee.psxandroid.scecompcall://redirect/?code=v3.CODE&cid=1"},
        )
    )
    session.post_queue.append(FakeResp(json_data=_token_json()))
    auth = PsnAuth(session)
    tokens = await auth.authenticate_with_npsso("npsso-token")
    assert tokens.access_token == "access-abc"
    assert tokens.refresh_token == "refresh-xyz"
    # authorize request must not follow redirects and must send the cookie
    method, url, kwargs = session.requests[0]
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Cookie"] == "npsso=npsso-token"


async def test_authenticate_bad_npsso_raises():
    session = FakeSession()
    session.get_queue.append(FakeResp(status=302, headers={"Location": "https://x/?error=login_required"}))
    auth = PsnAuth(session)
    with pytest.raises(PsnFamilyAuthError):
        await auth.authenticate_with_npsso("bad")


async def test_access_token_refresh_when_expired():
    session = FakeSession()
    now = time.time()
    expired = TokenSet("old", "refresh-xyz", now - 10, now + 99999)
    session.post_queue.append(FakeResp(json_data=_token_json()))
    auth = PsnAuth(session, tokens=expired)
    token = await auth.async_get_access_token()
    assert token == "access-abc"  # refreshed
    method, url, kwargs = session.requests[0]
    assert kwargs["data"]["grant_type"] == "refresh_token"


async def test_no_tokens_raises_auth_error():
    auth = PsnAuth(FakeSession())
    with pytest.raises(PsnFamilyAuthError):
        await auth.async_get_access_token()


# --- Client GraphQL error mapping ------------------------------------------


def _client_with(session, *, valid_token=True):
    client = OhanaClient(session=session)
    now = time.time()
    exp = now + 3600 if valid_token else now - 10
    client._auth._tokens = TokenSet("access-abc", "refresh-xyz", exp, now + 99999)
    return client


async def test_scope_wall_http_403():
    session = FakeSession()
    session.post_queue.append(FakeResp(status=403, json_data={}))
    client = _client_with(session)
    with pytest.raises(PsnFamilyScopeError):
        await client.get_family_members()


async def test_scope_wall_graphql_unauthorized():
    session = FakeSession()
    session.post_queue.append(
        FakeResp(json_data={"errors": [{"message": "no", "extensions": {"code": "UNAUTHORIZED"}}]})
    )
    client = _client_with(session)
    with pytest.raises(PsnFamilyScopeError):
        await client.get_family_members()


async def test_generic_graphql_error():
    session = FakeSession()
    session.post_queue.append(
        FakeResp(json_data={"errors": [{"message": "boom", "extensions": {"code": "INTERNAL"}}]})
    )
    client = _client_with(session)
    with pytest.raises(PsnFamilyApiError):
        await client.get_family_members()


async def test_get_family_members_success():
    session = FakeSession()
    session.post_queue.append(
        FakeResp(json_data={"data": {"familyMembers": [
            {"id": "m1", "identity": {"accountId": "1", "displayName": "Kid", "familyRole": "CHILD", "ageGroup": "CHILD"}},
            {"id": "m2", "identity": {"accountId": "2", "displayName": "Parent", "familyRole": "FAMILY_MANAGER", "ageGroup": "ADULT"}},
        ]}})
    )
    client = _client_with(session)
    members = await client.get_family_members()
    assert len(members) == 2
    children = [m for m in members if m.identity.is_child]
    assert len(children) == 1 and children[0].identity.display_name == "Kid"


async def test_401_triggers_refresh_and_retry():
    session = FakeSession()
    # First POST -> 401, then token refresh POST, then retried op POST -> 200.
    session.post_queue.append(FakeResp(status=401, json_data={}))
    session.post_queue.append(FakeResp(json_data=_token_json()))
    session.post_queue.append(FakeResp(json_data={"data": {"familyMembers": []}}))
    client = _client_with(session)
    members = await client.get_family_members()
    assert members == []
    # Ensure a refresh happened between the two op calls.
    grant_types = [r[2].get("data", {}).get("grant_type") for r in session.requests if r[0] == "POST"]
    assert "refresh_token" in grant_types


async def test_update_todays_playtime_builds_variables():
    session = FakeSession()
    session.post_queue.append(
        FakeResp(json_data={"data": {"updateTodaysPlaytimeLimit": {"success": True}}})
    )
    client = _client_with(session)
    ok = await client.update_todays_playtime("member-1", "PT30M")
    assert ok is True
    body = session.requests[0][2]["json"]
    assert body["operationName"] == "updateTodaysPlaytimeLimit"
    assert body["variables"] == {"familyMemberId": "member-1", "playtimeDurationChange": "PT30M"}


async def test_add_remove_time_quantize_and_sign():
    session = FakeSession()
    session.post_queue.append(FakeResp(json_data={"data": {"updateTodaysPlaytimeLimit": {"success": True}}}))
    session.post_queue.append(FakeResp(json_data={"data": {"updateTodaysPlaytimeLimit": {"success": True}}}))
    client = _client_with(session)
    await client.add_time("m", 25 * 60)     # -> quantize to 30m -> "PT30M"
    await client.remove_time("m", 25 * 60)  # -> "-PT30M"
    assert session.requests[0][2]["json"]["variables"]["playtimeDurationChange"] == "PT30M"
    assert session.requests[1][2]["json"]["variables"]["playtimeDurationChange"] == "-PT30M"
