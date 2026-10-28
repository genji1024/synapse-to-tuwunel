#!/usr/bin/env python3
"""Export a stopped Synapse into the intermediate format (docs/format.md).

Usage: export.py HOMESERVER_YAML OUT_DIR

Reads the database, signing key and media store named in HOMESERVER_YAML.
Synapse must be stopped, or at least idle: the export runs in one read-only
snapshot, but media files are copied outside it.

Needs PyYAML and psycopg2 (Synapse's own virtualenv has both).
"""

import json
import os
import shutil
import sys

import psycopg2
import yaml

FORMAT_VERSION = 1


def main(config_path, out):
    config = yaml.safe_load(open(config_path))
    db = config["database"]
    if db["name"] != "psycopg2":
        sys.exit("only PostgreSQL databases are supported")
    args = {k: v for k, v in db["args"].items() if k not in ("cp_min", "cp_max")}

    os.makedirs(out)
    conn = psycopg2.connect(**args)
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
    cur = conn.cursor()
    server_name = config["server_name"]
    counts = {}

    def rows(sql, *params):
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        for row in cur:
            yield dict(zip(names, row))

    def write(name, items):
        n = 0
        with open(os.path.join(out, name), "w") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                n += 1
        counts[name] = n

    write("users.jsonl", ({
        "user_id": r["name"],
        "password_hash": r["password_hash"],
        "created_ts": r["creation_ts"] * 1000 if r["creation_ts"] else None,
        "admin": bool(r["admin"]),
        "guest": bool(r["is_guest"]),
        "deactivated": bool(r["deactivated"]),
        "locked": bool(r["locked"]),
        "suspended": bool(r["suspended"]),
        "user_type": r["user_type"],
        "appservice_id": r["appservice_id"],
        "displayname": r["displayname"],
        "avatar_url": r["avatar_url"],
        "profile_fields": r["fields"] or {},
    } for r in rows("""
        SELECT u.*, p.displayname, p.avatar_url, p.fields
        FROM users u LEFT JOIN profiles p ON p.full_user_id = u.name
        ORDER BY u.name""")))

    write("account_data.jsonl", ({
        "user_id": r["user_id"], "room_id": r["room_id"], "type": r["type"], "content": json.loads(r["content"]),
    } for r in rows("""
        SELECT user_id, NULL AS room_id, account_data_type AS type, content FROM account_data
        UNION ALL
        SELECT user_id, room_id, account_data_type, content FROM room_account_data
        ORDER BY user_id, room_id NULLS FIRST, type""")))

    aliases, extremities, current_state = {}, {}, {}
    for r in rows("SELECT room_alias, room_id FROM room_aliases ORDER BY room_alias"):
        aliases.setdefault(r["room_id"], []).append(r["room_alias"])
    for r in rows("SELECT room_id, event_id FROM event_forward_extremities ORDER BY event_id"):
        extremities.setdefault(r["room_id"], []).append(r["event_id"])
    for r in rows("SELECT room_id, type, state_key, event_id FROM current_state_events ORDER BY type, state_key"):
        current_state.setdefault(r["room_id"], []).append([r["type"], r["state_key"], r["event_id"]])
    write("rooms.jsonl", ({
        "room_id": r["room_id"],
        "room_version": r["room_version"],
        "creator": r["creator"],
        "published": bool(r["is_public"]),
        "aliases": aliases.get(r["room_id"], []),
        "forward_extremities": extremities.get(r["room_id"], []),
        "current_state": current_state.get(r["room_id"], []),
    } for r in rows("SELECT * FROM rooms ORDER BY room_id")))

    # Synapse stores each group either in full or as a delta on prev_state_group.
    write("state_groups.jsonl", ({
        "id": r["id"], "room_id": r["room_id"], "prev": r["prev"], "state": r["state"] or [],
    } for r in rows("""
        SELECT g.id, g.room_id, e.prev_state_group AS prev,
               (SELECT json_agg(json_build_array(s.type, s.state_key, s.event_id) ORDER BY s.type, s.state_key)
                FROM state_groups_state s WHERE s.state_group = g.id) AS state
        FROM state_groups g LEFT JOIN state_group_edges e ON e.state_group = g.id
        ORDER BY g.id""")))

    write("events.jsonl", ({
        "event_id": r["event_id"],
        "room_id": r["room_id"],
        "stream_ordering": r["stream_ordering"],
        "outlier": r["outlier"],
        "rejected": r["reason"],
        "state_group": r["state_group"],
        "redacted_by": r["redacted_by"],
        "pdu": json.loads(r["json"]),
    } for r in rows("""
        SELECT e.event_id, e.room_id, e.stream_ordering, e.outlier, j.json, rj.reason, sg.state_group,
               (SELECT min(rd.event_id) FROM redactions rd WHERE rd.redacts = e.event_id) AS redacted_by
        FROM events e
        JOIN event_json j USING (event_id)
        LEFT JOIN rejections rj USING (event_id)
        LEFT JOIN event_to_state_groups sg USING (event_id)
        ORDER BY e.stream_ordering""")))

    media_store = config.get("media_store_path", "media_store")
    os.makedirs(os.path.join(out, "media"))
    media = list(rows("""
        SELECT media_id, media_type, media_length, created_ts, upload_name, user_id, sha256,
               quarantined_by IS NOT NULL AS quarantined, url_cache IS NOT NULL AS url_cache
        FROM local_media_repository ORDER BY media_id"""))
    for m in media:
        mid = m["media_id"]
        src = os.path.join(media_store, "local_content", mid[0:2], mid[2:4], mid[4:])
        m["mxc"] = f"mxc://{server_name}/{mid}"
        m["file"] = None
        if os.path.exists(src):
            m["file"] = f"media/{mid}"
            shutil.copyfile(src, os.path.join(out, m["file"]))
    write("media.jsonl", media)

    write("signing_keys.jsonl", signing_keys(config))

    cur.execute("SELECT version FROM schema_version")
    schema = cur.fetchone()[0]
    conn.rollback()
    json.dump({
        "format_version": FORMAT_VERSION,
        "server_name": server_name,
        "synapse_schema_version": schema,
        "counts": counts,
    }, open(os.path.join(out, "manifest.json"), "w"), indent=2)
    print(json.dumps(counts))


def signing_keys(config):
    """The current key (with its seed) and old verify-only keys."""
    with open(config["signing_key_path"]) as f:
        for line in f:
            alg, version, seed = line.split()
            yield {"key_id": f"{alg}:{version}", "seed": seed, "expired_ts": None}
    for key_id, old in (config.get("old_signing_keys") or {}).items():
        yield {"key_id": key_id, "verify_key": old["key"], "expired_ts": old["expired_ts"]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
