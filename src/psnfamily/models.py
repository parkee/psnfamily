"""Typed models for PS Family API responses.

Shapes are reverse-engineered from the app's GraphQL documents. Field domains
that are server-driven or unconfirmed are kept as plain strings; see
research/PROTOCOL.md. ``from_dict`` builders are defensive against missing
keys because cloud responses vary by account/region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .codec import parse_pt


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """Safely walk nested dict keys, returning ``default`` if any is missing."""
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


@dataclass(slots=True)
class Identity:
    """A family member's identity."""

    account_id: str
    display_name: str = ""
    online_id: str = ""
    family_role: str = ""
    age_group: int | None = None
    country: str = ""
    locale: str = ""
    date_of_birth: str = ""
    display_picture_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Identity:
        """Build from the ``identity`` selection set."""
        age = data.get("ageGroup")
        return cls(
            account_id=str(data.get("accountId") or ""),
            display_name=data.get("displayName") or "",
            online_id=data.get("onlineId") or "",
            family_role=(data.get("familyRole") or "").upper(),
            age_group=int(age) if isinstance(age, int) else None,
            country=data.get("country") or "",
            locale=data.get("locale") or "",
            date_of_birth=data.get("dateOfBirth") or "",
            display_picture_url=_get(data, "displayPicture", "url", default=""),
        )

    @property
    def is_child(self) -> bool:
        """True if this member is a child (subject to parental controls)."""
        return self.family_role == "CHILD"


@dataclass(slots=True)
class FamilyMember:
    """A member of the PSN family (adult or child)."""

    member_id: str
    identity: Identity
    parental_controls: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FamilyMember:
        """Build from a ``familyMembers[]`` entry."""
        identity = Identity.from_dict(data.get("identity") or {})
        pc = _get(data, "parentalControls", default={}) or {}
        return cls(
            member_id=str(data.get("id") or identity.account_id),
            identity=identity,
            parental_controls=pc if isinstance(pc, dict) else {},
        )


@dataclass(slots=True)
class DateTimeRange:
    """An absolute time window."""

    start: str = ""
    end: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DateTimeRange:
        data = data or {}
        return cls(start=data.get("start") or "", end=data.get("end") or "")


@dataclass(slots=True)
class PlaytimeLimit:
    """One play-time limit row (a recurrence + allowed duration + windows)."""

    duration: str = ""
    recurrence: str = ""
    next_range: DateTimeRange = field(default_factory=DateTimeRange)
    windows: list[DateTimeRange] = field(default_factory=list)

    @property
    def duration_seconds(self) -> int:
        """Allowed play-time for this row, in seconds."""
        return parse_pt(self.duration)

    @property
    def is_blocked(self) -> bool:
        """True if this row allows **no** play-time at all.

        PSN encodes "no play allowed" as a zero duration (``P0D`` / ``PT0S``);
        it is *not* an "unlimited" sentinel — a child with a ``P0D`` limit is
        blocked, not unrestricted.
        """
        return parse_pt(self.duration) == 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaytimeLimit:
        windows = [
            DateTimeRange.from_dict(w.get("dateTimeRange"))
            for w in (data.get("windows") or [])
            if isinstance(w, dict)
        ]
        return cls(
            duration=data.get("duration") or "",
            recurrence=(data.get("recurrence") or "").upper(),
            next_range=DateTimeRange.from_dict(data.get("nextDateTimeRange")),
            windows=windows,
        )


@dataclass(slots=True)
class Usage:
    """Time actually played within a window."""

    duration: str = ""
    range: DateTimeRange = field(default_factory=DateTimeRange)

    @property
    def duration_seconds(self) -> int:
        """Time played in this window, in seconds."""
        return parse_pt(self.duration)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            duration=data.get("duration") or "",
            range=DateTimeRange.from_dict(data.get("dateTimeRange")),
        )


@dataclass(slots=True)
class Playtime:
    """A child's play-time configuration and usage."""

    account_id: str = ""
    timezone_id: str = ""
    utc_offset_minutes: int = 0
    on_limit_reached: str = ""
    limits: list[PlaytimeLimit] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    last_updated: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], account_id: str = "") -> Playtime:
        """Build from ``familyMember.playtime``."""
        pt = data or {}
        tz = pt.get("timezone") or {}
        limit_settings = pt.get("limitSettings") or {}
        limits = [
            PlaytimeLimit.from_dict(limit)
            for limit in (limit_settings.get("limits") or [])
            if isinstance(limit, dict)
        ]
        history = pt.get("history") or {}
        usages = [
            Usage.from_dict(u)
            for u in (history.get("usages") or [])
            if isinstance(u, dict)
        ]
        return cls(
            account_id=account_id,
            timezone_id=tz.get("id") or tz.get("name") or "",
            utc_offset_minutes=int(tz.get("utcOffsetInMinutes") or 0),
            on_limit_reached=(limit_settings.get("onLimitReached") or "").upper(),
            limits=limits,
            usages=usages,
            last_updated=history.get("lastUpdatedDate") or "",
        )

    # --- Derived helpers (no direct GraphQL field) --------------------------

    @property
    def today_limit(self) -> PlaytimeLimit | None:
        """The limit row that applies today.

        A one-day override (recurrence ``ONCE``) wins; else a ``DAILY`` row;
        else the first row (the weekly schedule is returned today-first).
        """
        if not self.limits:
            return None
        for rec in ("ONCE", "DAILY"):
            for limit in self.limits:
                if limit.recurrence == rec:
                    return limit
        return self.limits[0]

    @property
    def today_limit_seconds(self) -> int | None:
        """Today's effective limit in seconds (one-day override or schedule).

        ``0`` means *blocked* — no play allowed today (PSN's ``P0D``). ``None``
        means no limit row exists at all, i.e. play-time is unrestricted.
        """
        limit = self.today_limit
        if limit is None:
            return None
        return limit.duration_seconds  # P0D -> 0 (blocked), never "unlimited"

    @property
    def recurring_limit_seconds(self) -> int | None:
        """The recurring per-day limit in seconds (the weekly schedule value).

        Ignores any one-day (``ONCE``) override and returns the first recurring
        (``WEEKLY``/``DAILY``) row, which PSN returns soonest-day-first. ``0`` =
        blocked every day (``P0D``); ``None`` = no recurring schedule configured.
        """
        for limit in self.limits:
            if limit.recurrence in ("WEEKLY", "DAILY"):
                return limit.duration_seconds
        return None

    @property
    def used_today_seconds(self) -> int:
        """Seconds played 'today' in the child's timezone (best-effort)."""
        usage = self._today_usage()
        return usage.duration_seconds if usage else 0

    @property
    def remaining_seconds(self) -> int | None:
        """Seconds of play-time remaining today, clamped at 0.

        ``0`` when blocked (``P0D``) or the limit is used up. ``None`` only when
        no limit is configured at all (play-time is unrestricted).
        """
        limit = self.today_limit_seconds
        if limit is None:
            return None
        return max(0, limit - self.used_today_seconds)

    def _today_usage(self) -> Usage | None:
        """Pick the usage row covering 'now' in the child's timezone."""
        if not self.usages:
            return None
        now = datetime.now(UTC) + timedelta(minutes=self.utc_offset_minutes)
        today = now.date()
        for usage in self.usages:
            start = _parse_iso(usage.range.start)
            if start is not None:
                local = start + timedelta(minutes=self.utc_offset_minutes)
                if local.date() == today:
                    return usage
        # Fall back to the usage with the latest start.
        return max(
            self.usages,
            key=lambda u: _parse_iso(u.range.start) or datetime.min.replace(tzinfo=UTC),
        )


@dataclass(slots=True)
class Presence:
    """Online presence for a family member."""

    account_id: str = ""
    online_status: str = ""
    last_online_date: str = ""
    last_platform: str = ""
    now_playing_title: str = ""
    now_playing_platform: str = ""

    @property
    def is_online(self) -> bool:
        """True if currently online."""
        return self.online_status.upper() == "ONLINE"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Presence:
        """Build from a ``childPresences[]`` entry."""
        now_playing = data.get("nowPlayingTitle") or {}
        title = now_playing.get("title") or {}
        return cls(
            account_id=str(data.get("accountId") or ""),
            online_status=(data.get("onlineStatus") or "").upper(),
            last_online_date=data.get("lastOnlineDate") or "",
            last_platform=data.get("lastPlatform") or "",
            now_playing_title=title.get("name") or "",
            now_playing_platform=title.get("platform") or "",
        )


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
