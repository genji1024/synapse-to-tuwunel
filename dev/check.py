#!/usr/bin/env python3
"""Compare a migrated Tuwunel with what the export says it should hold.

Usage: check.py BASE_URL SEED_JSON PASSWORDS_TSV EXPORT_DIR
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE, SEED, PASSWORDS, EXPORT = sys.argv[1:5]
failures = []


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()}


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def q(s):
    return urllib.parse.quote(s, safe="")


seed = json.load(open(SEED))
passwords = dict(line.rstrip("\n").split("\t") for line in open(PASSWORDS))
users = {u["user_id"]: u for u in map(json.loads, open(f"{EXPORT}/users.jsonl"))}

for user_id, user in users.items():
    res = call("POST", "/_matrix/client/v3/login", body={
        "type": "m.login.password", "identifier": {"type": "m.id.user", "user": user_id},
        "password": passwords.get(user_id, ""),
    })
    check(f"login {user_id}", "access_token" in res, res)
    profile = call("GET", f"/_matrix/client/v3/profile/{q(user_id)}")
    check(f"displayname {user_id}", profile.get("displayname") == user["displayname"], profile)
    check(f"avatar {user_id}", profile.get("avatar_url") == user["avatar_url"], profile)

keys = call("GET", "/_matrix/key/v2/server")
want = [json.loads(line)["key_id"] for line in open(f"{EXPORT}/signing_keys.jsonl")][0]
check("signing key", want in keys.get("verify_keys", {}), keys)

print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
