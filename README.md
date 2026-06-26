# psnfamily

Async Python client for the **PlayStation Family** parental-controls API — the
cloud API behind Sony's *PS Family* mobile app (`com.playstation.ohana.android`).
It lets you read your family roster, children's online presence and now-playing
game, and **read and change a child's daily play-time limit**, plus content and
communication parental controls.

> ⚠️ **Unofficial.** Reverse-engineered from the PS Family Android app; not
> affiliated with or endorsed by Sony. The API is undocumented and may change or
> break at any time. Excessive use may risk your PSN account — the client
> self-limits to 1 request / 3 s. Use at your own risk.
>
> For turnkey use the library ships the PS Family app's own OAuth client
> credentials (the ones carrying the `mobile:family` scope), recovered from the
> app. Sony may rotate or revoke these at any time, which would require an
> update. You can override them via `OAuthClient(...)`.

## Auth

PSN uses OAuth; this library authenticates headlessly with an **npsso** cookie:

1. Sign in at <https://www.playstation.com> as the **Family Manager** account.
2. Open <https://ca.account.sony.com/api/v1/ssocookie> — copy the 64-char
   `npsso` value.
3. Pass it to `OhanaClient.authenticate(npsso)`. The library exchanges it for an
   access token (~1 h) and a refresh token (~60 days) and refreshes
   automatically. Persist `client.tokens` to avoid re-entering the npsso.

## Usage

```python
import asyncio
from psnfamily import OhanaClient

async def main():
    async with OhanaClient() as client:
        await client.authenticate("<npsso>")

        children = await client.get_children()
        for child in children:
            pt = await client.get_playtime(child.identity.account_id)
            print(child.identity.display_name,
                  "limit", pt.today_limit_seconds, "used", pt.used_today_seconds)

        kid = children[0]
        # Set a 2-hour recurring daily play-time limit:
        await client.set_daily_limit(kid.member_id, 2 * 3600)
        # Block play entirely (P0D); 0 / None do the same — NOT "unlimited":
        await client.set_daily_limit(kid.member_id, 0)

        # Grant 30 more minutes today only (one-day override):
        await client.add_time(kid, 30 * 60)
        # ...or set today's limit absolutely (0 clears the override):
        await client.set_today_limit(kid, 90 * 60)

asyncio.run(main())
```

Play-time is governed by a recurring weekly schedule of
`PlaytimeDaySetting { maxPlaytimeDuration, windowStart, windowEnd }`.
`set_daily_limit` applies a uniform limit across the week (quantized to 15 min);
`set_all_days_limit(member, seconds)` does the same but preserves each day's
window; `set_schedule_day(member, weekday, …)` edits a single weekday
(read-modify-write, leaving the rest intact); `set_playtime_schedule` writes the
raw 7-entry list. Read the structured schedule via `Playtime.weekly_schedule`
(7 `ScheduleDay`, Monday→Sunday). Durations are ISO-8601 (`PT2H`; `P0D` =
**blocked**, no play allowed — *not* unlimited); the playable-hours window is in
minutes from local midnight (full day = `0..1440`). A child has no play-time by
default until you grant it.

## Capabilities

- `authenticate(npsso)` / token refresh; persist `client.tokens` to reuse
- `get_family_members()` / `get_children()` — roster, roles, ids
- `get_presences(account_ids)` — online status + now-playing game
- `get_playtime(account_id)` — limits, usage, timezone, on-limit action
- `set_daily_limit(member_id, seconds)` / `set_all_days_limit(member, seconds)` /
  `set_schedule_day(member, weekday, …)` / `set_playtime_schedule(...)` — set the
  recurring weekly schedule (uniform, per-day, or raw); `Playtime.weekly_schedule`
  reads it back as 7 `ScheduleDay`
- `set_on_limit_action(member_id, action)` — `NOTIFY_ONLY` / `FORCE_LOGOUT`
- `set_today_limit(member, seconds)` — absolutely set **today only** (one-day
  override; `0` clears it, reverting to the schedule)
- `add_time(member, seconds)` / `remove_time(member, seconds)` — grant or take
  back today's play-time relatively (read-modify-write; never goes negative)
- `update_todays_playtime(member_id, change)` — low-level absolute set (the raw
  `updateTodaysPlaytimeLimit` op)
- `set_parental_control(account_id, field, value)` — set one content/communication
  control (`internetBrowser`, `vrApp`, `freeCommunication`, `contentControl`,
  `ageLevel`, `gameContent`, `spendingLimit`)
- `set_bulk_parental_controls(account_id, controls)` — apply several controls in
  one request (the only path that can write `bluerayAgeContent` /
  `discContentCountry` / `dvdContent`)
- `answer_game_exception(account_id, title_id, approve)` / `remove_game_exception(...)`
  — approve/deny a child's request to play a restricted game, or revoke an
  allow-listed game
- `get_supported_parental_controls()` and other read operations
- `execute(operation, variables)` — any reverse-engineered operation by name

> **Persisted-query hashes.** The gateway only accepts Sony-allowlisted
> persisted-query `sha256` hashes. **All** of them are reproduced offline from the
> app's operation documents by one recipe:
> `sha256(graphql@14.print(sortAST(addTypename(query))))`. The graphql **v14**
> printer is the key detail — it keeps long argument lists single-line, while
> v15/v16 reformat them, which is why an earlier v15 recipe matched short-argument
> ops but not the `updateParentalControls` family / `updateTodaysPlaytimeLimit`.
> The recipe was recovered by decompiling the app's persisted-query-id closure
> from the Hermes bytecode and every hash was verified against the live gateway
> safelist (see `research/CAPTURE_FINDINGS.md`). No live capture is required.

## Auth client

The library ships the PS Family app's OAuth client (the one carrying the
`mobile:family` scope), recovered from the app, analogous to how `psn-api` /
`PSNAWP` ship the public PSN mobile client. You only provide the `npsso`.

See `research/PROTOCOL.md` in the repository for the full reverse-engineering
reference and the list of facts still pending live confirmation.

## License

MIT
