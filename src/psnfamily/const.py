"""Constants for the PS Family ("ohana") PSN API.

Values are reverse-engineered from the PlayStation Family Android app
(com.playstation.ohana.android 26.4.0) and corroborated against the open PSN
ecosystem (psn-api, PSNAWP). See research/PROTOCOL.md for provenance tags.
"""

from __future__ import annotations

from typing import Final

# --- OAuth / account endpoints (Sony "Versa" SSO, prod env) -----------------
AUTHORIZE_URL: Final = "https://ca.account.sony.com/api/authz/v3/oauth/authorize"
TOKEN_URL: Final = "https://ca.account.sony.com/api/authz/v3/oauth/token"

# --- GraphQL data gateway ---------------------------------------------------
GRAPHQL_URL: Final = "https://m.np.playstation.com/api/graphql/v1/op"

# --- PS Family OAuth client -------------------------------------------------
# The PS Family app's own OAuth client. Its token carries the ``mobile:family``
# scope required by the ohana* family operations (the public PSN mobile client
# lacks it). Credentials were recovered by decrypting the app's encrypted
# client config (AES-CBC, key HMAC-derived from the signing cert) out of the
# native libsieohanares.so / libsieohanautil.so — see research/SCOPE_BLOCKER.md.
CLIENT_ID: Final = "90765a7f-5d37-49e0-8526-b86b459da220"
CLIENT_SECRET: Final = "ZEH6o8MjfVEK30ga"
REDIRECT_URI: Final = "com.playstation.ohana.android.scecompcall://redirect"
SCOPE: Final = "mobile:family"

# The public PSN mobile client (psn-api / PSNAWP default) authenticates but its
# token cannot reach the family API. Kept for reference / non-family use.
PUBLIC_CLIENT_ID: Final = "09515159-7237-4370-9b40-3806e67c0891"
PUBLIC_CLIENT_SECRET: Final = "ucPjka5tntB2KqsP"

# --- HTTP identification ----------------------------------------------------
# apollographql-client-* mirror the real app; only okhttp/4.12.0 is a literal
# found in the bundle. Sent to look like the app if the gateway is picky.
APOLLO_CLIENT_NAME: Final = "ohana"
APOLLO_CLIENT_VERSION: Final = "26.4.0"
USER_AGENT: Final = "com.playstation.ohana.android/26.4.0 (Android) okhttp/4.12.0"

# --- Token lifecycle --------------------------------------------------------
# Refresh the access token this many seconds before its stated expiry.
TOKEN_EXPIRY_MARGIN: Final = 60

# --- Rate limiting ----------------------------------------------------------
# PSNAWP self-imposes 1 request / 3 s and warns excessive use risks bans.
MIN_REQUEST_INTERVAL: Final = 3.0

# --- Defaults ---------------------------------------------------------------
DEFAULT_TIMEOUT: Final = 30.0
