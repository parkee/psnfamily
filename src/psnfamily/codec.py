"""ISO-8601 duration helpers and playtime derivations.

PS Family expresses every play-time value (limits, usage, deltas) as an
ISO-8601 duration string of the form ``PT<h>H<m>M<s>S`` (e.g. ``PT1H30M``).
``updateTodaysPlaytimeLimit`` takes a *signed delta* in the same format; the
negative spelling (``-PT30M`` vs ``PT-30M``) is configurable while it is
verified against the live API — see research/PROTOCOL.md §5.1.
"""

from __future__ import annotations

import re
from typing import Final

# Full ISO-8601 duration grammar (the API returns e.g. "P0D", "PT2H30M",
# "P0DT45M"). Weeks/days/hours/minutes/seconds; years/months are not used for
# play-time so are not accepted.
_DUR_RE: Final = re.compile(
    r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$",
    re.IGNORECASE,
)

# Picker granularity in the app is 15 minutes.
QUANTUM_SECONDS: Final = 15 * 60


def parse_pt(value: str | None) -> int:
    """Parse an ISO-8601 duration into seconds.

    Accepts the full ``P[nW][nD]T[nH][nM][nS]`` form (e.g. ``P0D``, ``PT2H30M``,
    ``P0DT45M``). Handles a leading ``-`` (``-PT30M``) and an inner ``PT-30M``.
    Returns 0 for ``None``/empty. Raises ``ValueError`` on malformed input.
    """
    if not value:
        return 0
    text = value.strip()
    negative = False
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if text.upper().startswith("PT-"):
        negative = not negative
        text = "PT" + text[3:]
    match = _DUR_RE.fullmatch(text)
    if not match:
        raise ValueError(f"Not an ISO-8601 duration: {value!r}")
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    total = (
        weeks * 604800
        + days * 86400
        + hours * 3600
        + minutes * 60
        + seconds
    )
    return -total if negative else total


def format_pt(seconds: int) -> str:
    """Format whole seconds as a canonical ``PT`` duration.

    Negative values use a leading ``-`` (``-PT30M``). Zero is ``PT0M``.
    """
    negative = seconds < 0
    seconds = abs(int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    body = "PT"
    if hours:
        body += f"{hours}H"
    if minutes:
        body += f"{minutes}M"
    if secs:
        body += f"{secs}S"
    if body == "PT":
        body = "PT0M"
    return f"-{body}" if negative else body


def quantize_seconds(seconds: int, quantum: int = QUANTUM_SECONDS) -> int:
    """Round seconds to the nearest ``quantum`` (default 15 min) like the UI."""
    if quantum <= 0:
        return int(seconds)
    return round(seconds / quantum) * quantum
