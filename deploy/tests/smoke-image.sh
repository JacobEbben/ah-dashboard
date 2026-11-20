#!/usr/bin/env bash
# Smoke test of a built image, from the outside (the image contains no tests).
# Starts it like production (read-only, no capabilities), WITHOUT an AH login
# and without contacting AH, and checks what unit tests can't see.
#   deploy/tests/smoke-image.sh <image>
set -euo pipefail
IMAGE="${1:?usage: smoke-image.sh <image>}"
NAME="ah-smoke-$$"
PORT="${SMOKE_PORT:-18733}"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "SMOKE FAIL: $*" >&2; docker logs --tail 30 "$NAME" >&2 || true; exit 1; }

docker run -d --name "$NAME" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  -v /app/instance -v /app/logs -p "127.0.0.1:$PORT:8733" \
  -e TOKEN_SEAL_SECRET="smoke-test-secret-$(date +%s%N)" -e BONUS_PREWARM=false \
  "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null && break
  sleep 1
done
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/healthz" || fail "/healthz does not answer"

headers="$(curl -fsS -D - -o /dev/null "http://127.0.0.1:$PORT/")"
grep -qi "^content-security-policy: default-src 'none'" <<< "$headers" || fail "no strict CSP on /"
grep -qi '^server:' <<< "$headers" && fail "a Server header is sent"
[[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/admin/")" == 404 ]] || fail "/admin exists without an admin password"
[[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/bonus")" == 503 ]] || fail "/api/bonus without an AH login should be 503"
docker exec "$NAME" sh -c 'touch /app/ah_dashboard/x' 2>/dev/null && fail "the code folder is writable"
[[ "$(docker inspect --format '{{.Config.User}}' "$IMAGE")" == app ]] || fail "the image does not run as the app user"
echo "smoke test passed: $IMAGE"
