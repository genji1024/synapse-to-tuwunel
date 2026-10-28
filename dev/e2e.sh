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

step() {
	printf '\n== %s\n' "$*"
	# On GitHub, also an annotation: the log itself cannot be read there.
	[ -z "${GITHUB_ACTIONS:-}" ] || echo "::notice::e2e: $*"
}

# wait_for URL PID LOG: until URL answers, giving up after two minutes or
# when the process is gone.
wait_for() {
	i=0
	until curl -sf "$1" >/dev/null; do
		if ! kill -0 "$2" 2>/dev/null || [ $((i += 1)) -gt 120 ]; then
			echo "$1 did not answer; the end of $3:"
			tail -n 50 "$3"
			exit 1
		fi
		sleep 1
	done
}

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
wait_for http://127.0.0.1:18008/health "$synapse" synapse.log
secret=$(venv/bin/python -c 'import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))["registration_shared_secret"])' synapse/homeserver.yaml)
timeout 600 python3 "$here/seed.py" http://127.0.0.1:18008 "$secret" seed.json
kill "$synapse"
wait "$synapse" || true

step "Export"
rm -rf export
timeout 600 venv/bin/python "$here/../exporter/export.py" synapse/homeserver.yaml export

step "Import"
cat > tuwunel.toml <<TOML
[global]
server_name = "s2t.test"
database_path = "$work/tuwunel-db"
address = ["127.0.0.1"]
port = 18448
# The check reads the signing key from the federation key endpoint; it
# listens on 127.0.0.1 only, so nothing federates.
allow_federation = true
allow_registration = false
new_user_displayname_suffix = ""
TOML
timeout 600 "$importer" keypair tuwunel.toml export
timeout 600 "$importer" import tuwunel.toml export passwords.tsv

step "Tuwunel"
TUWUNEL_CONFIG=tuwunel.toml "$tuwunel" > tuwunel.log 2>&1 &
server=$!
wait_for http://127.0.0.1:18448/_matrix/client/versions "$server" tuwunel.log
timeout 600 python3 "$here/check.py" http://127.0.0.1:18448 seed.json passwords.tsv export
kill "$server"
