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
        # Set a 2-hour daily play-time limit:
        await client.set_daily_limit(kid.member_id, 2 * 3600)
        # Remove the limit (unlimited):
        await client.set_daily_limit(kid.member_id, None)

asyncio.run(main())
```

Play-time is governed by a recurring weekly schedule of
`PlaytimeDaySetting { maxPlaytimeDuration, windowStart, windowEnd }`.
`set_daily_limit` applies a uniform limit across the week (quantized to 15 min);
`set_playtime_schedule` gives full per-day control. Durations are ISO-8601
(`PT2H`, `P0D` = no limit); the playable-hours window is in minutes from local
midnight (full day = `0..1440`).

## Capabilities

- `authenticate(npsso)` / token refresh; persist `client.tokens` to reuse
- `get_family_members()` / `get_children()` — roster, roles, ids
- `get_presences(account_ids)` — online status + now-playing game
- `get_playtime(account_id)` — limits, usage, timezone, on-limit action
- `set_daily_limit(member_id, seconds)` / `set_playtime_schedule(...)` — set
  play-time limits
- `set_on_limit_action(member_id, action)` — `NOTIFY_ONLY` / `FORCE_LOGOUT`
- `get_supported_parental_controls()` and other read operations
- `execute(operation, variables)` — any reverse-engineered operation by name

> Some write operations the app contains (e.g. `updateTodaysPlaytimeLimit` and
> the `updateParentalControls` family) are not in Sony's gateway allowlist for
> the current app version and will raise `PsnFamilyApiError` ("not whitelisted").

## Auth client

The library ships the PS Family app's OAuth client (the one carrying the
`mobile:family` scope), recovered from the app, analogous to how `psn-api` /
`PSNAWP` ship the public PSN mobile client. You only provide the `npsso`.

See `research/PROTOCOL.md` in the repository for the full reverse-engineering
reference and the list of facts still pending live confirmation.

## License

MIT
