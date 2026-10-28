#!/usr/bin/env python3
"""Fill a fresh Synapse with a small, known data set for migration tests.

Usage: seed.py BASE_URL SHARED_SECRET OUT_JSON

Creates two accounts (pochi, tama), their profiles and account data, a space
with a public room, a direct chat and a room tama left, with plain messages,
an edit, a reply, a reaction, a redaction and an uploaded image. Writes the
user and room IDs to OUT_JSON so later checks can compare both servers.
"""

import hashlib
import hmac
import json
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib

BASE, SECRET, OUT = sys.argv[1:4]
PASSWORD = "migrate-me"
txn = 0


def call(method, path, token=None, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: {e.code} {e.read().decode()}")


def register(localpart, admin):
    nonce = call("GET", "/_synapse/admin/v1/register")["nonce"]
    mac = hmac.new(SECRET.encode(), digestmod=hashlib.sha1)
    mac.update(b"\x00".join([nonce.encode(), localpart.encode(), PASSWORD.encode(), b"admin" if admin else b"notadmin"]))
    res = call("POST", "/_synapse/admin/v1/register", body={
        "nonce": nonce, "username": localpart, "password": PASSWORD, "admin": admin, "mac": mac.hexdigest(),
    })
    return res["user_id"], res["access_token"]


def send(token, room, body, etype="m.room.message"):
    global txn
    txn += 1
    path = f"/_matrix/client/v3/rooms/{q(room)}/send/{etype}/t{txn}"
    return call("PUT", path, token, body)["event_id"]


def state(token, room, etype, key, body):
    return call("PUT", f"/_matrix/client/v3/rooms/{q(room)}/state/{etype}/{q(key)}", token, body)["event_id"]


def q(s):
    return urllib.parse.quote(s, safe="")


def png(rgb):
    """A 2x2 PNG of one colour, so uploads need no image library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def upload(token, name, data):
    path = "/_matrix/media/v3/upload?filename=" + q(name)
    return call("POST", path, token, raw=data, ctype="image/png")["content_uri"]


pochi, pt = register("pochi", True)
tama, tt = register("tama", False)

call("PUT", f"/_matrix/client/v3/profile/{q(pochi)}/displayname", pt, {"displayname": "ポチ"})
call("PUT", f"/_matrix/client/v3/profile/{q(tama)}/displayname", tt, {"displayname": "タマ"})
avatar = upload(pt, "pochi.png", png((200, 120, 40)))
call("PUT", f"/_matrix/client/v3/profile/{q(pochi)}/avatar_url", pt, {"avatar_url": avatar})
call("PUT", f"/_matrix/client/v3/user/{q(pochi)}/account_data/test.s2t.note", pt, {"text": "global account data"})

space = call("POST", "/_matrix/client/v3/createRoom", pt, {
    "name": "おうち", "creation_content": {"type": "m.space"}, "preset": "private_chat", "invite": [tama],
})["room_id"]
oyatsu = call("POST", "/_matrix/client/v3/createRoom", pt, {
    "name": "おやつ", "topic": "今日のおやつ", "preset": "public_chat", "room_alias_name": "oyatsu",
})["room_id"]
state(pt, space, "m.space.child", oyatsu, {"via": ["s2t.test"]})
state(pt, oyatsu, "m.space.parent", space, {"via": ["s2t.test"], "canonical": True})
call("POST", f"/_matrix/client/v3/join/{q(space)}", tt, {})
call("POST", f"/_matrix/client/v3/join/{q(oyatsu)}", tt, {})

first = send(pt, oyatsu, {"msgtype": "m.text", "body": "ほねがほしい"})
send(tt, oyatsu, {"msgtype": "m.text", "body": "さかながいい", "m.relates_to": {"m.in_reply_to": {"event_id": first}}})
send(pt, oyatsu, {
    "msgtype": "m.text", "body": "* ほねが二本ほしい",
    "m.new_content": {"msgtype": "m.text", "body": "ほねが二本ほしい"},
    "m.relates_to": {"rel_type": "m.replace", "event_id": first},
})
send(tt, oyatsu, {"m.relates_to": {"rel_type": "m.annotation", "event_id": first, "key": "👍️"}}, "m.reaction")
oops = send(tt, oyatsu, {"msgtype": "m.text", "body": "まちがえた"})
call("PUT", f"/_matrix/client/v3/rooms/{q(oyatsu)}/redact/{q(oops)}/r1", tt, {"reason": "test"})
photo = upload(tt, "fish.png", png((40, 90, 200)))
send(tt, oyatsu, {"msgtype": "m.image", "body": "fish.png", "url": photo, "info": {"mimetype": "image/png", "w": 2, "h": 2}})
state(pt, oyatsu, "m.room.topic", "", {"topic": "明日のおやつ"})
call("PUT", f"/_matrix/client/v3/user/{q(pochi)}/rooms/{q(oyatsu)}/account_data/m.tag", pt, {"tags": {"m.favourite": {}}})

dm = call("POST", "/_matrix/client/v3/createRoom", pt, {"preset": "trusted_private_chat", "is_direct": True, "invite": [tama]})["room_id"]
call("POST", f"/_matrix/client/v3/join/{q(dm)}", tt, {})
send(pt, dm, {"msgtype": "m.text", "body": "さんぽいく？"})
send(tt, dm, {"msgtype": "m.text", "body": "いかない"})
call("PUT", f"/_matrix/client/v3/user/{q(pochi)}/account_data/m.direct", pt, {tama: [dm]})

left = call("POST", "/_matrix/client/v3/createRoom", pt, {"name": "ひみつ", "preset": "private_chat", "invite": [tama]})["room_id"]
call("POST", f"/_matrix/client/v3/join/{q(left)}", tt, {})
send(tt, left, {"msgtype": "m.text", "body": "ばいばい"})
call("POST", f"/_matrix/client/v3/rooms/{q(left)}/leave", tt, {})

json.dump({
    "server_name": pochi.split(":", 1)[1], "password": PASSWORD,
    "users": {"pochi": pochi, "tama": tama},
    "rooms": {"space": space, "oyatsu": oyatsu, "dm": dm, "left": left},
    "media": {"avatar": avatar, "photo": photo},
}, open(OUT, "w"), ensure_ascii=False, indent=2)
print(f"seeded {OUT}")
