#!/usr/bin/env bash
# Functional test of the server-side deploy flow, on this machine's Docker,
# with everything renamed (ahtest-*) so nothing else is touched:
#   a local registry, test images built from the app image, the rendered
#   compose files and ah-deploy.conf, and Caddy with its internal CA.
# Scenarios: first deploy, idempotent deploy, wrong repository refused,
# upgrade with backup, broken image -> automatic rollback, manual rollback
# to an image that refuses the newer database -> backup restored, restart,
# backup, the SSH forced command, Caddy (HTTPS, redirects, headers,
# X-Forwarded-For can't be faked). Never contacts AH.
#
#   deploy/tests/deploy-flow.sh <rendered dir>   (run.sh passes it)
set -euo pipefail

RENDERED="${1:?usage: deploy-flow.sh <rendered functional dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY="$ROOT/deploy/ansible/roles/app/files/ah-deploy"
DEPLOY_SSH="$ROOT/deploy/ansible/roles/app/files/ah-deploy-ssh"
REGISTRY_PORT="${TEST_REGISTRY_PORT:-5055}"
REPO="127.0.0.1:$REGISTRY_PORT/ah-dashboard"
WORK="$(mktemp -d)"
export AH_DEPLOY_CONF="$WORK/ah-deploy.conf"

pass() { printf '  \033[32mok\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*" >&2; docker logs --tail 30 ahtest-app >&2 2>/dev/null || true; exit 1; }
cleanup() {
  docker rm -f ahtest-app ahtest-caddy ahtest-registry >/dev/null 2>&1 || true
  docker volume rm -f ahtest-instance ahtest-logs ahtest-caddy-data ahtest-caddy-config >/dev/null 2>&1 || true
  docker network rm ahtest-edge >/dev/null 2>&1 || true
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^(ahtest-|$REPO)" | xargs -r docker rmi -f >/dev/null 2>&1 || true
  # Backups are written by root (in a container): remove them the same way.
  docker run --rm -v "$WORK:/w" alpine:3.20 sh -c 'rm -rf /w/*' >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
cleanup_leftovers() { docker rm -f ahtest-app ahtest-caddy ahtest-registry >/dev/null 2>&1 || true; }
cleanup_leftovers

# ---- setup -------------------------------------------------------------------------------
mkdir -p "$WORK/app" "$WORK/backups" "$WORK/caddy/conf"
cp "$RENDERED/app-compose.yaml" "$WORK/app/compose.yaml"
cp "$RENDERED/app-app.env" "$WORK/app/app.env"
cp "$RENDERED/caddy-compose.yaml" "$WORK/caddy/compose.yaml"
cp "$RENDERED/caddy-Caddyfile" "$WORK/caddy/conf/Caddyfile"
# The rendered conf, with this test's folders and repository.
sed -e "s#^COMPOSE_DIR=.*#COMPOSE_DIR=$WORK/app#" -e "s#^BACKUP_DIR=.*#BACKUP_DIR=$WORK/backups#" \
    -e "s#^IMAGE_REPO=.*#IMAGE_REPO=$REPO#" -e "s#^PUBLIC_URL=.*#PUBLIC_URL=#" \
    "$RENDERED/app-ah-deploy.conf" > "$AH_DEPLOY_CONF"
echo "LOCK_FILE=$WORK/lock" >> "$AH_DEPLOY_CONF"

docker network create --ipv6 ahtest-edge >/dev/null
docker run -d --name ahtest-registry -p "127.0.0.1:$REGISTRY_PORT:5000" registry:3.1.1 >/dev/null
for _ in $(seq 1 20); do curl -fs "http://127.0.0.1:$REGISTRY_PORT/v2/" >/dev/null && break; sleep 0.5; done

echo "  building test images"
docker build -q -t ahtest-base "$ROOT" >/dev/null
# v1 behaves like an OLDER app: it refuses a database marked as upgraded.
# v2 behaves like a NEWER app: it upgrades (marks) the database.
# broken never starts.
build_variant() {
  printf 'FROM ahtest-base\nLABEL test.variant=%s\nCMD %s\n' "$1" "$2" | docker build -q -t "ahtest-$1" - >/dev/null
  docker tag "ahtest-$1" "$REPO:$1"
  docker push -q "$REPO:$1" >/dev/null
  docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$REPO:$1" | grep "^$REPO@" | head -1
}
V1="$(build_variant v1 '["sh", "-c", "test ! -e /app/instance/upgraded || { echo refusing newer database; exit 1; }; exec python serve.py"]')"
V2="$(build_variant v2 '["sh", "-c", "touch /app/instance/upgraded; exec python serve.py"]')"
BROKEN="$(build_variant broken '["python", "-c", "raise SystemExit(\"broken on purpose\")"]')"
# Deploys pull from the registry: remove the local copies.
docker rmi -f "$REPO:v1" "$REPO:v2" "$REPO:broken" >/dev/null

state() { cat "$WORK/app/state/$1" 2>/dev/null || true; }
has_marker() { docker run --rm -v ahtest-instance:/d:ro alpine:3.20 test -e /d/upgraded; }
nbackups() { find "$WORK/backups" -name '*.tar.gz' | wc -l; }

# ---- ah-deploy -------------------------------------------------------------------------------
"$DEPLOY" deploy "$V1" 2>/dev/null || fail "first deploy"
[[ "$(state current)" == "$V1" && "$(docker inspect -f '{{.State.Health.Status}}' ahtest-app)" == healthy ]] \
  || fail "first deploy: state/health"
pass "first deploy: healthy, recorded (no data yet, so no backup: $(nbackups))"

out="$("$DEPLOY" deploy "$V1" 2>&1)" || fail "idempotent deploy"
grep -q "already running" <<< "$out" || fail "idempotent deploy"
pass "deploying the running image again changes nothing"

if "$DEPLOY" deploy "docker.io/evil/image@sha256:$(printf '0%.0s' {1..64})" 2>/dev/null; then fail "foreign image accepted"; fi
if "$DEPLOY" deploy "$REPO:latest" 2>/dev/null; then fail "tag (not digest) accepted"; fi
pass "images from another repository, or by tag, are refused"

"$DEPLOY" deploy "$V2" 2>/dev/null || fail "upgrade to v2"
[[ "$(state current)" == "$V2" && "$(state previous)" == "$V1" && "$(nbackups)" == 1 && -n "$(state rollback_backup)" ]] \
  || fail "upgrade: state/backup"
has_marker || fail "v2 did not upgrade the database"
pass "upgrade to v2: backup taken first, previous = v1, database upgraded"

if "$DEPLOY" deploy "$BROKEN" 2>"$WORK/broken.log"; then fail "broken image reported success"; fi
grep -q "rolled back; the database was kept" "$WORK/broken.log" || { cat "$WORK/broken.log"; fail "no automatic rollback"; }
[[ "$(state current)" == "$V2" && "$(docker inspect -f '{{.State.Health.Status}}' ahtest-app)" == healthy ]] \
  || fail "after broken deploy: not back on a healthy v2"
pass "broken image: detected, rolled back to v2 automatically (database kept)"

"$DEPLOY" rollback 2>"$WORK/rollback.log" || { cat "$WORK/rollback.log"; fail "manual rollback"; }
grep -q "restored backup" "$WORK/rollback.log" || { cat "$WORK/rollback.log"; fail "backup not restored"; }
[[ "$(state current)" == "$V1" && "$(state previous)" == "$V2" ]] || fail "rollback: state"
has_marker && fail "rollback: the restored database is still the upgraded one"
pass "manual rollback to v1: v1 refused the newer database -> the backup from before v2 was restored"

"$DEPLOY" restart 2>/dev/null || fail "restart"
before="$(nbackups)"; "$DEPLOY" backup 2>/dev/null || fail "backup"
[[ "$(nbackups)" -gt "$before" && "$(docker inspect -f '{{.State.Health.Status}}' ahtest-app)" == healthy ]] \
  || fail "backup: no new file or app not back"
status_out="$("$DEPLOY" status)"
grep -qF "current:     $V1" <<< "$status_out" || { echo "$status_out"; fail "status"; }
pass "restart, backup (app back up afterwards) and status work"

(exec 9>"$WORK/lock"; flock 9; "$DEPLOY" status >/dev/null 2>"$WORK/lock.log") && fail "ran while locked"
grep -q "another ah-deploy is running" "$WORK/lock.log" || fail "lock message"
pass "two ah-deploy runs at once are refused"

# ---- the SSH forced command -------------------------------------------------------------------------
FAKE_SUDO="$WORK/fake-sudo"
printf '#!/bin/sh\nprintf "%%s\\n" "$*"\n' > "$FAKE_SUDO"; chmod 755 "$FAKE_SUDO"
ssh_cmd() { SSH_ORIGINAL_COMMAND="$1" AH_DEPLOY_SUDO="$FAKE_SUDO" "$DEPLOY_SSH" 2>/dev/null; }
# shellcheck disable=SC2016  # the literal $(id) is the point: it must be refused, not run
for bad in "" "rm -rf /" "status; id" "status extra" "deploy" "deploy $REPO:latest" \
           "deploy $REPO@sha256:abc" "deploy $V1 extra" "restart" "backup" 'deploy $(id)@sha256:'"$(printf 'a%.0s' {1..64})"; do
  if ssh_cmd "$bad" >/dev/null; then fail "forced command accepted: '$bad'"; fi
done
[[ "$(ssh_cmd "status")" == "-n /usr/local/bin/ah-deploy status" ]] || fail "forced command: status"
[[ "$(ssh_cmd "deploy $V2")" == "-n /usr/local/bin/ah-deploy deploy $V2" ]] || fail "forced command: deploy"
pass "SSH forced command: only 'deploy <image@digest>', 'status', 'rollback' pass"

# ---- Caddy in front -----------------------------------------------------------------------------------
docker compose --project-directory "$WORK/caddy" up -d >/dev/null 2>&1 || fail "caddy did not start"
for _ in $(seq 1 30); do
  curl -ksf --resolve ah.test:18443:127.0.0.1 -o /dev/null https://ah.test:18443/healthz && break; sleep 1
done
headers="$(curl -ksS --resolve ah.test:18443:127.0.0.1 -D - -o /dev/null https://ah.test:18443/)" || fail "no HTTPS answer"
grep -qi "^content-security-policy: default-src 'none'" <<< "$headers" || fail "CSP missing through Caddy"
grep -qi "^strict-transport-security:" <<< "$headers" || fail "HSTS missing (ADMIN_COOKIE_SECURE)"
grep -qiE "^(server|via):" <<< "$headers" && fail "Caddy sends a Server or Via header"
grep -qi "^content-encoding: \(zstd\|gzip\)" <<< "$(curl -ksS --resolve ah.test:18443:127.0.0.1 -H 'Accept-Encoding: gzip' \
  -D - -o /dev/null https://ah.test:18443/)" || fail "no compression"
pass "HTTPS through Caddy: CSP + HSTS from the app, no Server/Via header, compressed"

loc="$(curl -sS --resolve ah.test:18080:127.0.0.1 -o /dev/null -w '%{http_code} %{redirect_url}' http://ah.test:18080/x)"
# Caddy redirects to the standard HTTPS port (443 in production; this test's
# 18443 is only a test port), keeping the path.
[[ "$loc" == 30[18]\ https://ah.test/x || "$loc" == 30[18]\ https://ah.test:18443/x ]] || fail "HTTP -> HTTPS redirect ($loc)"
loc="$(curl -ksS --resolve www.ah.test:18443:127.0.0.1 -o /dev/null -w '%{http_code} %{redirect_url}' https://www.ah.test:18443/y)"
[[ "$loc" == 30[18]\ https://ah.test/y || "$loc" == 30[18]\ https://ah.test:18443/y ]] || fail "www redirect ($loc)"
pass "HTTP redirects to HTTPS; www.<domain> redirects to <domain>"

# A faked X-Forwarded-For must not reach the app's lockouts and limits.
jar="$WORK/cookies"
csrf="$(curl -ksS -c "$jar" --resolve ah.test:18443:127.0.0.1 https://ah.test:18443/admin/login \
  | sed -nE 's/.*name="csrf" value="([^"]+)".*/\1/p' | head -1)"
[[ -n "$csrf" ]] || fail "no admin login form through Caddy"
curl -ksS -b "$jar" --resolve ah.test:18443:127.0.0.1 -H "X-Forwarded-For: 6.6.6.6" -o /dev/null \
  --data-urlencode "csrf=$csrf" --data-urlencode "password=wrong-password" https://ah.test:18443/admin/login
sleep 1
logline="$(docker logs ahtest-app 2>&1 | grep "failed admin login from" | tail -1)"
[[ -n "$logline" ]] || fail "no failed-login log line"
grep -q "6.6.6.6" <<< "$logline" && fail "a client could fake its address: $logline"
pass "X-Forwarded-For from a client is replaced by Caddy (${logline##* from })"

docker exec ahtest-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || fail "caddy reload"
pass "caddy reload (the Ansible handler's command) works"
