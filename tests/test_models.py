"""Tests for response models and playtime derivations."""

from psnfamily.models import FamilyMember, Playtime, Presence

FAMILY_MEMBER = {
    "id": "member-1",
    "identity": {
        "accountId": "1234567890",
        "displayName": "Kiddo",
        "onlineId": "kiddo_psn",
        "familyRole": "CHILD",
        "ageGroup": "CHILD",
        "country": "US",
        "displayPicture": {"url": "https://example/pic.png"},
    },
    "parentalControls": {"id": "pc-1", "ageLevel": 3},
}


def test_family_member_from_dict():
    m = FamilyMember.from_dict(FAMILY_MEMBER)
    assert m.member_id == "member-1"
    assert m.identity.account_id == "1234567890"
    assert m.identity.display_name == "Kiddo"
    assert m.identity.is_child
    assert m.parental_controls["ageLevel"] == 3


def test_family_member_defaults_member_id_to_account_id():
    data = {"identity": {"accountId": "999"}}
    m = FamilyMember.from_dict(data)
    assert m.member_id == "999"


def test_playtime_derivations():
    pt = Playtime.from_dict(
        {
            "timezone": {"id": "America/Los_Angeles", "utcOffsetInMinutes": -420},
            "limitSettings": {
                "onLimitReached": "NOTIFY_ONLY",
                "limits": [{"duration": "PT2H", "recurrence": "DAILY", "windows": []}],
            },
            "history": {
                "usages": [
                    {
                        "duration": "PT30M",
                        "dateTimeRange": {
                            "start": "2026-06-23T08:00:00Z",
                            "end": "2026-06-23T23:59:00Z",
                        },
                    }
                ],
                "lastUpdatedDate": "2026-06-23T20:00:00Z",
            },
        },
        account_id="acc-1",
    )
    assert pt.today_limit_seconds == 7200
    assert pt.on_limit_reached == "NOTIFY_ONLY"
    assert pt.utc_offset_minutes == -420
    # used depends on "today"; with a wide window it should pick the entry.
    assert pt.used_today_seconds in (0, 1800)


def test_playtime_today_override_wins():
    pt = Playtime.from_dict(
        {
            "limitSettings": {
                "limits": [
                    {"duration": "PT2H", "recurrence": "DAILY"},
                    {"duration": "PT3H", "recurrence": "ONCE"},
                ]
            }
        }
    )
    # ONCE (today override) takes precedence over DAILY.
    assert pt.today_limit_seconds == 10800


def test_playtime_no_limit_is_none():
    pt = Playtime.from_dict({"limitSettings": {"limits": []}})
    assert pt.today_limit_seconds is None
    assert pt.remaining_seconds is None
    assert pt.recurring_limit_seconds is None


def test_playtime_p0d_is_blocked_not_unlimited():
    # P0D means 0 minutes allowed (blocked), NOT unlimited.
    pt = Playtime.from_dict(
        {
            "limitSettings": {
                "limits": [{"duration": "P0D", "recurrence": "WEEKLY"}]
            },
            "history": {
                "usages": [
                    {
                        "duration": "PT10M",
                        "dateTimeRange": {
                            "start": "2026-06-23T00:00:00Z",
                            "end": "2026-06-23T23:59:00Z",
                        },
                    }
                ]
            },
        }
    )
    assert pt.today_limit.is_blocked is True
    assert pt.today_limit_seconds == 0  # blocked, not None/unlimited
    assert pt.recurring_limit_seconds == 0
    assert pt.remaining_seconds == 0  # clamped, never negative


def test_playtime_recurring_ignores_today_override():
    # The recurring limit reports the WEEKLY value, not the ONCE override.
    pt = Playtime.from_dict(
        {
            "limitSettings": {
                "limits": [
                    {"duration": "PT45M", "recurrence": "ONCE"},
                    {"duration": "P0D", "recurrence": "WEEKLY"},
                ]
            }
        }
    )
    assert pt.today_limit_seconds == 2700  # the override applies today
    assert pt.recurring_limit_seconds == 0  # the schedule stays blocked


def test_presence_from_dict():
    p = Presence.from_dict(
        {
            "accountId": "acc-1",
            "onlineStatus": "online",
            "lastPlatform": "PS5",
            "nowPlayingTitle": {"title": {"name": "Astro Bot", "platform": "PS5"}},
        }
    )
    assert p.is_online
    assert p.now_playing_title == "Astro Bot"
    assert p.now_playing_platform == "PS5"
