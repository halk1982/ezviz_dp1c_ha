#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
repo_dir="$PWD"
test_network="dp1c-smoke-$$"
test_broker="dp1c-broker-$$"
docker network create --internal "$test_network" >/dev/null
trap 'docker rm -f "$test_broker" >/dev/null 2>&1 || true; docker network rm "$test_network" >/dev/null 2>&1 || true' EXIT INT TERM
docker run -d --name "$test_broker" --network "$test_network" --network-alias broker \
  -v "$repo_dir/tests/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" eclipse-mosquitto:2 >/dev/null
attempt=0
until docker exec "$test_broker" mosquitto_pub -h localhost -t ready -m ready; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 10 ] || exit 1
  sleep 1
done
docker run --rm --network "$test_network" \
  -v "$repo_dir/tests/mqtt_smoke.py:/test/mqtt_smoke.py:ro" \
  ezviz-dp1c:test python /test/mqtt_smoke.py
