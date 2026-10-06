#!/usr/bin/env bash
# (Re)creates the local dev stack from scratch: throwaway Synapse "localhost" without rate limits,
# Postgres for the plugin, users adminbot (server admin), admin, prof, ta, student (password
# devpass1234567) and dev/standalone.yaml with the bot's token. Deletes all previous dev data.
# --bots also starts the second maubot for professor bots and enables user_bots in the config.
set -euo pipefail
cd "$(dirname "$0")"

PASSWORD=devpass1234567
BOTS=false
[ "${1:-}" = "--bots" ] && BOTS=true

docker compose --profile bots down -v
docker compose run --rm synapse generate
docker compose run --rm --entrypoint sh synapse -c 'cat >> /data/homeserver.yaml' <<'EOF'

# dev only: no rate limits
rc_message: {per_second: 1000, burst_count: 1000}
rc_login:
  address: {per_second: 1000, burst_count: 1000}
  account: {per_second: 1000, burst_count: 1000}
  failed_attempts: {per_second: 1000, burst_count: 1000}
rc_joins:
  local: {per_second: 1000, burst_count: 1000}
rc_invites:
  per_room: {per_second: 1000, burst_count: 1000}
  per_user: {per_second: 1000, burst_count: 1000}
  per_issuer: {per_second: 1000, burst_count: 1000}
rc_room_creation: {per_second: 1000, burst_count: 1000}
EOF
docker compose up -d
until curl -sf http://localhost:8008/health >/dev/null; do sleep 1; done

for user in adminbot admin prof ta student; do
  flag=--no-admin
  [ "$user" = adminbot ] && flag=-a
  docker compose exec -T synapse register_new_matrix_user -c /data/homeserver.yaml \
    -u "$user" -p "$PASSWORD" "$flag" http://localhost:8008
done

token=$(curl -s -XPOST http://localhost:8008/_matrix/client/v3/login -d \
  "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"adminbot\"},\"password\":\"$PASSWORD\"}" \
  | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
sed "s|access_token: CHANGE-ME|access_token: $token|" standalone.example.yaml > standalone.yaml

if $BOTS; then
  docker compose --profile bots run --rm --no-deps --entrypoint sh userbots -c 'cat > /data/config.yaml' \
    < userbots-config.yaml
  docker compose --profile bots up -d userbots
  until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:29317/_matrix/maubot/v1/version)" != 000 ]; do sleep 1; done
  sed -i.bak -e 's|^    enabled: false|    enabled: true|' \
    -e 's|^    maubot_url: .*|    maubot_url: http://127.0.0.1:29317|' \
    -e 's|^    password: ""|    password: '"$PASSWORD"'|' \
    -e 's|^    homeserver_url: .*|    homeserver_url: http://synapse:8008|' standalone.yaml
  rm -f standalone.yaml.bak
  echo "userbots maubot ready on http://127.0.0.1:29317 (UI login: adminbot / $PASSWORD)"
fi

echo "Dev stack ready. Start the bot from server/admin-bot/:"
echo "  .venv/bin/python -m maubot.standalone -m maubot.yaml -c dev/standalone.yaml -b dev/standalone.example.yaml"
