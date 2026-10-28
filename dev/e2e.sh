#!/bin/sh
# Migrate a seeded Synapse into Tuwunel and check the result.
#
# Usage: dev/e2e.sh WORK_DIR IMPORTER TUWUNEL
#
# Needs a PostgreSQL reachable as the `postgres` user on localhost (PGHOST,
# PGPORT and PGPASSWORD are honoured), python3, and the two binaries.
set -eu

work=$(realpath "$1") importer=$(realpath "$2") tuwunel=$(realpath "$3")
here=$(dirname "$(realpath "$0")")
mkdir -p "$work"
cd "$work"

step() { printf '\n== %s\n' "$*"; }

step "Synapse"
psql -U postgres -c "CREATE USER synapse PASSWORD 'synapse'"
psql -U postgres -c "CREATE DATABASE synapse OWNER synapse ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
python3 -m venv venv
venv/bin/pip install -q psycopg2-binary matrix-synapse
mkdir -p synapse
venv/bin/python -m synapse.app.homeserver --server-name s2t.test \
	--config-path synapse/homeserver.yaml --generate-config --report-stats=no
venv/bin/python - synapse/homeserver.yaml <<'PY'
import sys, yaml
path = sys.argv[1]
c = yaml.safe_load(open(path))
c["database"] = {"name": "psycopg2", "args": {
    "user": "synapse", "password": "synapse", "dbname": "synapse",
    "host": "localhost", "cp_min": 1, "cp_max": 3}}
c["listeners"] = [{"port": 18008, "bind_addresses": ["127.0.0.1"], "type": "http",
                   "tls": False, "resources": [{"names": ["client"]}]}]
c["federation_domain_whitelist"] = []
c["trusted_key_servers"] = []
fast = {"per_second": 1000, "burst_count": 1000}
for k in ("rc_message", "rc_registration", "rc_room_creation"):
    c[k] = fast
c["rc_login"] = {"address": fast, "account": fast}
c["rc_joins"] = {"local": fast}
c["rc_invites"] = {"per_room": fast, "per_user": fast}
yaml.safe_dump(c, open(path, "w"))
PY
venv/bin/python -m synapse.app.homeserver -c synapse/homeserver.yaml > synapse.log 2>&1 &
synapse=$!
until curl -sf http://127.0.0.1:18008/health >/dev/null; do sleep 1; done
secret=$(venv/bin/python -c 'import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))["registration_shared_secret"])' synapse/homeserver.yaml)
python3 "$here/seed.py" http://127.0.0.1:18008 "$secret" seed.json
kill "$synapse"
wait "$synapse" || true

step "Export"
rm -rf export
venv/bin/python "$here/../exporter/export.py" synapse/homeserver.yaml export

step "Import"
cat > tuwunel.toml <<TOML
[global]
server_name = "s2t.test"
database_path = "$work/tuwunel-db"
address = ["127.0.0.1"]
port = 18448
allow_federation = false
allow_registration = false
new_user_displayname_suffix = ""
TOML
"$importer" keypair tuwunel.toml export
"$importer" import tuwunel.toml export passwords.tsv

step "Tuwunel"
TUWUNEL_CONFIG=tuwunel.toml "$tuwunel" > tuwunel.log 2>&1 &
server=$!
until curl -sf http://127.0.0.1:18448/_matrix/client/versions >/dev/null; do
	kill -0 "$server" || { cat tuwunel.log; exit 1; }
	sleep 1
done
python3 "$here/check.py" http://127.0.0.1:18448 seed.json passwords.tsv export
kill "$server"
