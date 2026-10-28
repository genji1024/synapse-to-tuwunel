# Intermediate format, version 1

A directory written by `exporter/export.py`. Every `*.jsonl` file holds one
JSON object per line, UTF-8.

## manifest.json

| field | meaning |
|---|---|
| `format_version` | `1` |
| `server_name` | the homeserver's name; the import keeps it |
| `synapse_schema_version` | Synapse's database schema version |
| `counts` | lines written to each file |

## signing_keys.jsonl

The current key carries `seed` (unpadded base64 of the ed25519 seed, as in
Synapse's key file). Old keys from `old_signing_keys` carry `verify_key` and
`expired_ts` instead. `key_id` is `ed25519:<version>`.

## users.jsonl

`user_id`, `password_hash` (bcrypt, `null` for accounts without a password),
`created_ts` (ms), flags `admin`, `guest`, `deactivated`, `locked`,
`suspended`, `user_type`, `appservice_id`, and the profile: `displayname`,
`avatar_url`, `profile_fields` (extra profile keys).

## account_data.jsonl

`user_id`, `room_id` (`null` for global account data), `type`, `content`.

## rooms.jsonl

| field | meaning |
|---|---|
| `room_id`, `room_version`, `creator` | |
| `published` | listed in the public room directory |
| `aliases` | local aliases pointing at the room |
| `forward_extremities` | the latest events, the `prev_events` of the next one |
| `current_state` | `[type, state_key, event_id]` triples |

## state_groups.jsonl

Synapse's state snapshots, ascending by `id`. A group with `prev: null`
lists its full state; otherwise `state` holds only the entries that differ
from group `prev`. Entries are `[type, state_key, event_id]`.

## events.jsonl

Ascending by `stream_ordering`, the order the server accepted them.

| field | meaning |
|---|---|
| `event_id`, `room_id`, `stream_ordering` | |
| `pdu` | the event as stored, signatures included. Content of redacted events is still present unless Synapse already censored it |
| `outlier` | stored without state, not part of the timeline |
| `rejected` | rejection reason, or `null` |
| `state_group` | state group of the state after this event |
| `redacted_by` | the redaction event, if any |

## media.jsonl and media/

Local uploads only. `media_id`, `mxc`, `media_type`, `media_length`,
`created_ts`, `upload_name`, `user_id`, `sha256`, `quarantined`, `url_cache`,
and `file`: the path of the copied content, or `null` when Synapse's file is
missing. Thumbnails and remote media are not exported.
