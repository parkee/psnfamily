"""psnfamily — Python client for the PlayStation Family parental-controls API.

Reverse-engineered from the PlayStation Family Android app. Unofficial and not
affiliated with Sony. See README.md and research/PROTOCOL.md.
"""

from .auth import OAuthClient, PsnAuth, TokenSet
from .client import OhanaClient
from .codec import format_pt, parse_pt, quantize_seconds
from .exceptions import (
    PsnFamilyApiError,
    PsnFamilyAuthError,
    PsnFamilyConnectionError,
    PsnFamilyError,
    PsnFamilyScopeError,
    PsnFamilyTimeoutError,
)
from .models import (
    DateTimeRange,
    FamilyMember,
    Identity,
    Playtime,
    PlaytimeLimit,
    Presence,
    Usage,
)

__version__ = "0.3.1"

__all__ = [
    "DateTimeRange",
    "FamilyMember",
    "Identity",
    "OAuthClient",
    "OhanaClient",
    "Playtime",
    "PlaytimeLimit",
    "Presence",
    "PsnAuth",
    "PsnFamilyApiError",
    "PsnFamilyAuthError",
    "PsnFamilyConnectionError",
    "PsnFamilyError",
    "PsnFamilyScopeError",
    "PsnFamilyTimeoutError",
    "TokenSet",
    "Usage",
    "format_pt",
    "parse_pt",
    "quantize_seconds",
]
