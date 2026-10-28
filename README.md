# synapse-to-tuwunel

Move a [Synapse](https://github.com/element-hq/synapse) homeserver to
[Tuwunel](https://github.com/matrix-construct/tuwunel) under the same server
name and signing key, so that accounts, rooms and history carry over.

Status: experiment. Tracking issue upstream:
[matrix-construct/tuwunel#2](https://github.com/matrix-construct/tuwunel/issues/2).

## How it works

Two separate steps, joined by a plain intermediate format
([docs/format.md](docs/format.md)):

1. **Export** (`exporter/`, Python): reads a stopped Synapse's PostgreSQL
   database, signing key and media store, and writes JSON Lines plus the
   media files.
2. **Import** (planned, Rust): writes the export into a new Tuwunel database
   through Tuwunel's own code, so the result is what Tuwunel itself would
   have stored.

## Scope of the first version

Carried over: the signing key, accounts and profiles, account data, rooms
with all their events and state, aliases, room directory entries, media.

Not yet: devices and end-to-end encryption keys, read receipts, pushers,
federation state, appservices. Passwords need Tuwunel to accept Synapse's
bcrypt hashes; until then users set a new one.

## Usage

```sh
# with Synapse stopped, using its virtualenv (PyYAML and psycopg2)
python exporter/export.py /path/to/homeserver.yaml export/
```

`dev/seed.py` fills an empty Synapse with a small data set for tests.

## License

Apache-2.0. No code is taken from Synapse (AGPL-3.0); the exporter only
reads its database.
