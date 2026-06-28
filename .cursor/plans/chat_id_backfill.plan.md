# Backfill chat_ids from group titles

## Goal

Link Mongo/Postgres identities (`gg_player_id` + nickname) to Telegram support groups by scanning stored group titles and merging `chat_id` into Postgres `player_details.chat_ids`.

## Match rule

Group title format: `SHORTHAND / GG-ID / tail` (existing `parse_group_title_parts`).

| Priority | Match on |
|---|---|
| 1 | `parsed.gg_player_id` == target `gg_player_id` |
| 2 | (optional `--nickname-fallback`) `parsed.tail` ≈ nickname, only if gg_id match absent |

Skip analytics test titles (`/ TEST`, `@jz034`).

## Sources

- **Targets:** gg-computer Mongo `player_details` (`clubId`, `gg_id`, `nickname`) via `GET /player-details/by-club`
- **Group scan (resolve → TG):** Postgres `groups` + `support_group_chats` (title from `groups.name` else `telegram_chat_title`)
- **Writes:** Postgres `player_details.chat_ids`

## Script

`scripts/backfill_player_chat_ids_from_titles.py`

```
--club-id N | --club-slug aces-table | --all-clubs
--apply          # default dry-run
--nickname-fallback
--json
```

Per `(club_id, gg_player_id)`:
1. Find matching chat(s) from title index
2. Dry-run: report would-bind / ambiguous / no-match
3. Apply: `bind_chat_to_player()` (merge, don't replace existing unless `--replace`)

## Output

Summary: scanned groups, players considered, bound, already had chat, ambiguous (2+ chats same gg_id), unmatched.

## Tests

Unit: title index builder, gg_id match, ambiguous detection, analytics exclusion.

## Out of scope

- MTProto live dialog scan (see `backfill_gc_player_bindings.py`)
- aon-beta Mongo writes
- Nickname-only match without gg_id (unless flag)

## Depends on

`bot/services/player_details.py`, `api/payments_helpers.is_analytics_excluded_group_title`, `api/club_slug`
