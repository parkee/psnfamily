"""Exceptions for the psnfamily library."""

from __future__ import annotations


class PsnFamilyError(Exception):
    """Base class for all psnfamily errors."""


class PsnFamilyConnectionError(PsnFamilyError):
    """Raised when the PSN servers cannot be reached."""


class PsnFamilyTimeoutError(PsnFamilyConnectionError):
    """Raised when a request to PSN times out."""


class PsnFamilyAuthError(PsnFamilyError):
    """Raised when authentication fails.

    Typically means the npsso is invalid/expired or the refresh token has
    died, and the user must supply a fresh npsso.
    """


class PsnFamilyScopeError(PsnFamilyError):
    """Raised when the token is valid but not authorized for the family API.

    This is the "scope wall": the OAuth client/account can authenticate but
    the PSN GraphQL gateway rejects the ``ohana*`` family operations (HTTP 403
    or a GraphQL ``UNAUTHORIZED`` / ``PERMISSION_DENIED`` error). Usually means
    the account is not the Family Manager, or the public OAuth client lacks the
    family scope.
    """


class PsnFamilyApiError(PsnFamilyError):
    """Raised when the GraphQL gateway returns errors for an operation.

    ``code`` carries the first GraphQL ``extensions.code`` when present.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Initialize with a message and optional GraphQL error code."""
        super().__init__(message)
        self.code = code
