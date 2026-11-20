#!/usr/bin/env bash
# Full deployment rehearsal on a SIMULATED VPS: a privileged Debian 12 +
# systemd container (deploy/tests/vps-sim/Dockerfile) with sshd, set up by the
# real tools in the real order:
#   ah-iac configure (answers piped in)  ->  ah-iac provision (twice: idempotent)
#   -> the Release workflow's build steps (build, smoke test, Trivy, push to a
#      TLS registry playing GHCR)  ->  deploy over the restricted CI key
#   -> HTTPS through Caddy  ->  upgrade, broken release (automatic rollback),
#      ah-iac rollback  ->  settings change via the inventory  ->  VPS reboot.
#
# Differences from a real VPS: Caddy uses tls_mode "internal" (Let's Encrypt
# can't validate a simulated domain); the registry has a self-signed CA that
# the VPS trusts; GitHub Actions itself is replaced by its steps.
# Never contacts AH. Takes ~10 minutes; needs ~1.5 GB free disk (removed after).
#
#   deploy/tests/vps-sim.sh            run, then remove everything
#   KEEP=1 deploy/tests/vps-sim.sh     leave the simulation running to look around
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIM="$(mktemp -d)"
NET=ahsim-net
SUBNET=172.30.99.0/24
CLIENT_IP=172.30.99.50                # the "visitor" (curl) container's address
VPS=ahsim-vps
DOMAIN=ah.sim
IMAGE_REPO="ahsim-registry:5000/owner/ah-dashboard"   # what the VPS pulls (plays ghcr.io/<owner>/ah-dashboard)
PUSH_REPO="127.0.0.1:5056/owner/ah-dashboard"         # the same registry, as the build machine reaches it
INV="deploy/ansible/inventory/sim"
ADMIN_PASSWORD="sim-admin-password-$RANDOM-not-real"
CURL_IMAGE=curlimages/curl:8.23.0

export AH_INVENTORY=sim AH_IAC_HOME="$SIM/home" AH_SSH_KEY="$SIM/admin_key" AH_IAC_DOCKER_ARGS="--network $NET"

t0=$SECONDS
# Never stop silently: say where the harness itself failed.
trap 'printf "\n  \033[31mFAIL\033[0m harness stopped at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
phase() { printf '\n\033[1m== %s\033[0m  (%ss)\n' "$*" "$((SECONDS - t0))"; }
pass() { printf '  \033[32mok\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }
# Read ALL input, then match. A bare `cmd | grep -q` can fail under pipefail:
# grep exits at the first match and cmd dies of SIGPIPE writing the rest.
has() { local input; input="$(cat)"; grep -q "$@" <<< "$input"; }
strip() { sed 's/\x1b\[[0-9;]*m//g'; }

cleanup() {
  if [[ "${KEEP:-0}" == 1 ]]; then
    echo; echo "KEEP=1: still running. VPS shell: docker exec -it $VPS bash   Remove: KEEP=0 $0 --cleanup-only"
    return
  fi
  docker rm -f "$VPS" ahsim-registry >/dev/null 2>&1 || true
  # Docker's and containerd's storage need real volumes: overlayfs can't sit on
  # the container's own overlay root (a real VPS has an ordinary disk there).
  docker volume rm -f ahsim-vps-docker ahsim-vps-containerd >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(ahsim-app|127\.0\.0\.1:5056/)' | xargs -r docker rmi -f >/dev/null 2>&1 || true
  # Files written by root in containers (inventory, certificates).
  docker run --rm -v "$ROOT/deploy/ansible/inventory:/i" -v "$SIM:/s" alpine:3.20 sh -c 'rm -rf /i/sim /s/*' >/dev/null 2>&1 || true
  rm -rf "$SIM"
}
trap cleanup EXIT
if [[ "${1:-}" == --cleanup-only ]]; then exit 0; fi
cleanup_start() { KEEP=0 cleanup; SIM="$(mktemp -d)"; export AH_IAC_HOME="$SIM/home" AH_SSH_KEY="$SIM/admin_key"; }
cleanup_start

vps() { docker exec "$VPS" "$@"; }
# A visitor on the network: curl with Caddy's (internal) root CA, real TLS verification.
visitor() {
  docker run --rm --network "$NET" --ip "$CLIENT_IP" -v "$SIM/caddy-root.crt:/ca.crt:ro" "$CURL_IMAGE" \
    --cacert /ca.crt --resolve "$DOMAIN:443:$VPS_IP" --resolve "www.$DOMAIN:443:$VPS_IP" \
    --resolve "$DOMAIN:80:$VPS_IP" -sS "$@"
}
# GitHub Actions' deploy step: ssh with the CI deploy key and the inventory's known_hosts.
ci_ssh() {
  docker run --rm --network "$NET" -v "$SIM/home/deploy_key:/k:ro" -v "$ROOT/$INV/known_hosts:/kh:ro" ah-iac-tools \
    ssh -i /k -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/kh \
    "ah-deploy@$VPS" "$@"
}
app_health() { vps docker inspect -f '{{.State.Health.Status}}' ah-dashboard 2>/dev/null || echo missing; }
in_toolbox() { docker run --rm -v "$ROOT:/work" -w /work ah-iac-tools "$@"; }

# ---- 1. the simulated VPS and registry ----------------------------------------------------------
phase "1. Simulated VPS (Debian 12, systemd, sshd) and a TLS registry playing GHCR"
docker build -q -t ahsim-vps-image "$ROOT/deploy/tests/vps-sim" >/dev/null
docker build -q -t ah-iac-tools -f "$ROOT/deploy/tools/Dockerfile" "$ROOT/deploy" >/dev/null
docker network create --subnet "$SUBNET" "$NET" >/dev/null

mkdir -p "$SIM/certs"
docker run --rm -v "$SIM/certs:/c" -w /c alpine:3.20 sh -c '
  apk add -q openssl >/dev/null &&
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=sim-registry-ca -keyout ca.key -out ca.crt 2>/dev/null &&
  openssl req -newkey rsa:2048 -nodes -subj /CN=ahsim-registry -keyout reg.key -out reg.csr 2>/dev/null &&
  printf "subjectAltName=DNS:ahsim-registry,IP:127.0.0.1" > ext &&
  openssl x509 -req -in reg.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 -extfile ext -out reg.crt 2>/dev/null &&
  chmod 644 *'
docker run -d --name ahsim-registry --network "$NET" -p 127.0.0.1:5056:5000 -v "$SIM/certs:/certs:ro" \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/reg.crt -e REGISTRY_HTTP_TLS_KEY=/certs/reg.key registry:3.1.1 >/dev/null

docker run -d --name "$VPS" --hostname "$VPS" --network "$NET" --privileged --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock -v ahsim-vps-docker:/var/lib/docker -v ahsim-vps-containerd:/var/lib/containerd \
  ahsim-vps-image >/dev/null
for _ in $(seq 1 30); do [[ "$(vps systemctl is-system-running 2>/dev/null)" =~ ^(running|degraded)$ ]] && break; sleep 1; done
VPS_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" "$VPS")"
# Like a cloud provider: your key for root (cloud-init), nothing else.
ssh-keygen -q -t ed25519 -N "" -C sim-admin -f "$SIM/admin_key"
docker cp "$SIM/admin_key.pub" "$VPS:/root/.ssh/authorized_keys"
vps chown root:root /root/.ssh/authorized_keys
# The registry's CA (GHCR has a public certificate; this one is self-signed).
vps mkdir -p /etc/docker/certs.d/ahsim-registry:5000
docker cp "$SIM/certs/ca.crt" "$VPS:/etc/docker/certs.d/ahsim-registry:5000/ca.crt"
vps sshd -T | has "^passwordauthentication yes" || fail "the fresh VPS should allow passwords (like a new server)"
vps sh -c 'command -v docker' >/dev/null && fail "Docker should not be installed yet"
pass "VPS $VPS ($VPS_IP) boots with systemd: no Docker, password SSH allowed (a fresh server)"

# ---- 2. configure ------------------------------------------------------------------------------
phase "2. deploy/ah-iac configure (answers piped in, like typing them)"
answers=("$VPS" root 22 "$DOMAIN" internal y "$IMAGE_REPO" y privacy@example.nl "" "" ""
         "$ADMIN_PASSWORD" "$ADMIN_PASSWORD" "" n y y)
printf '%s\n' "${answers[@]}" | "$ROOT/deploy/ah-iac" configure > "$SIM/configure.log" 2>&1 \
  || { strip < "$SIM/configure.log" | tail -20; fail "configure"; }
grep -q "Saved to ansible/inventory/sim/known_hosts" "$SIM/configure.log" || { strip < "$SIM/configure.log" | tail; fail "host keys not saved"; }
# No AH login in the simulation: keep the Bonus loader off (it would find no login anyway).
in_toolbox python3 - <<'EOF'
import yaml
p = "deploy/ansible/inventory/sim/group_vars/all/vars.yml"
text = open(p).read()
head = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
data = yaml.safe_load(text)
data["app_settings"]["BONUS_PREWARM"] = "false"
open(p, "w").write(head + yaml.safe_dump(data, sort_keys=False))
EOF
[[ "$(head -c 14 "$ROOT/$INV/group_vars/all/vault.yml")" == "\$ANSIBLE_VAULT" ]] || fail "vault.yml is not encrypted"
grep -q "$ADMIN_PASSWORD" "$ROOT/$INV/group_vars/all/vault.yml" && fail "the admin password is in the vault file"
pass "inventory written; vault encrypted (no password in it); host keys confirmed; CI deploy key created"

# ---- 3. provision --------------------------------------------------------------------------------
phase "3. deploy/ah-iac provision (base, docker, caddy, app) - first run"
"$ROOT/deploy/ah-iac" provision > "$SIM/provision1.log" 2>&1 || { strip < "$SIM/provision1.log" | tail -40; fail "provision"; }
recap1="$(strip < "$SIM/provision1.log" | grep -E "^vps +:" | tail -1)"
echo "  $recap1"
grep -q "No image deployed yet" "$SIM/provision1.log" || fail "provision should say nothing is deployed yet"
pass "first provision ran without errors"

phase "4. provision again: nothing may change (idempotent)"
"$ROOT/deploy/ah-iac" provision > "$SIM/provision2.log" 2>&1 || { strip < "$SIM/provision2.log" | tail -40; fail "provision (2nd)"; }
recap2="$(strip < "$SIM/provision2.log" | grep -E "^vps +:" | tail -1)"
echo "  $recap2"
if ! grep -q "changed=0 " <<< "$recap2"; then
  strip < "$SIM/provision2.log" | grep -B3 "^changed:" | grep "^TASK" | sed 's/^/    changed: /'
  fail "the second provision changed something"
fi
pass "second provision: changed=0"

phase "5. The server as Ansible left it"
vps ufw status | has "Status: active" || fail "firewall not active"
for rule in "22/tcp.*ALLOW" "80/tcp.*ALLOW" "443/tcp.*ALLOW" "443/udp.*ALLOW"; do
  vps ufw status | has -E "$rule" || fail "firewall rule $rule missing"
done
pass "firewall active: SSH, 80, 443 tcp+udp; everything else denied"
sshd_conf="$(vps sshd -T)"
if ! grep -q "^passwordauthentication no" <<< "$sshd_conf" || ! grep -q "^permitrootlogin without-password" <<< "$sshd_conf"; then
  fail "SSH not hardened"
fi
out="$(docker run --rm --network "$NET" ah-iac-tools ssh -o BatchMode=yes -o StrictHostKeyChecking=no \
  -o PubkeyAuthentication=no -o PreferredAuthentications=password "root@$VPS" true 2>&1 || true)"
grep -q "Permission denied (publickey)" <<< "$out" || fail "password login still offered: $out"
pass "SSH: keys only (a password login is refused before asking for a password)"
[[ "$(vps cat /etc/apt/apt.conf.d/20auto-upgrades)" == *'Unattended-Upgrade "1"'* ]] || fail "no automatic updates"
pass "automatic security updates on"
vps docker info --format '{{.LoggingDriver}} live-restore={{.LiveRestoreEnabled}}' | has "^local live-restore=true" \
  || fail "Docker daemon settings"
vps docker network inspect edge --format '{{.EnableIPv6}}' | has true || fail "edge network without IPv6"
[[ "$(vps docker inspect -f '{{.State.Status}}' caddy)" == running ]] || fail "Caddy not running"
pass "Docker (log rotation, live-restore), edge network with IPv6, Caddy running"
[[ "$(vps stat -c '%a %U' /opt/ah-dashboard/app.env)" == "600 root" ]] || fail "app.env permissions"
vps grep -q "^TOKEN_SEAL_SECRET='" /opt/ah-dashboard/app.env || fail "seal secret missing from app.env"
vps grep -q '^restrict,command="/usr/local/bin/ah-deploy-ssh" ssh-ed25519' /home/ah-deploy/.ssh/authorized_keys \
  || fail "deploy key not restricted"
vps sudo -l -U ah-deploy | has "(root) NOPASSWD: /usr/local/bin/ah-deploy$" || fail "sudo rule"
pass "app.env root-only (600) with the vault's secrets; CI key restricted to ah-deploy-ssh; sudo only for ah-deploy"

# ---- 6. release v1 ------------------------------------------------------------------------------------
# The Release workflow's build job, step by step; returns the image reference the server pulls.
release() {
  local tag="$1" cmd="${2:-}" digest
  if [[ -z "$cmd" ]]; then
    docker build -q -t "ahsim-app:$tag" --label "org.opencontainers.image.revision=$tag" "$ROOT" >/dev/null
    bash "$ROOT/deploy/tests/smoke-image.sh" "ahsim-app:$tag" >/dev/null || fail "smoke test of $tag"
  else
    # Fails only on the server (a real CI smoke test would stop a plain crash earlier).
    printf 'FROM ahsim-app:v1\nLABEL org.opencontainers.image.revision=%s\nCMD %s\n' "$tag" "$cmd" \
      | docker build -q -t "ahsim-app:$tag" - >/dev/null
  fi
  docker tag "ahsim-app:$tag" "$PUSH_REPO:sha-$tag"
  docker push -q "$PUSH_REPO:sha-$tag" >/dev/null
  digest="$(docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$PUSH_REPO:sha-$tag" | grep "^$PUSH_REPO@" | head -1)"
  echo "$IMAGE_REPO@${digest#*@}"
}

phase "6. Release v1: build, smoke test, Trivy, push; deploy over the CI key"
V1="$(release v1)"
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:0.76.1 image --quiet --exit-code 1 \
  --severity CRITICAL --ignore-unfixed ahsim-app:v1 >/dev/null || fail "Trivy found fixable critical issues"
pass "built, smoke-tested, scanned and pushed: ${V1##*@}"
ci_ssh "deploy $V1" > "$SIM/deploy1.log" 2>&1 || { cat "$SIM/deploy1.log"; fail "deploy v1"; }
[[ "$(app_health)" == healthy ]] || fail "app not healthy after deploy"
vps docker port ah-dashboard | has . && fail "the app publishes a port"
pass "deployed over the restricted key; healthy; no published port"

vps docker exec caddy cat /data/caddy/pki/authorities/local/root.crt > "$SIM/caddy-root.crt"
for _ in $(seq 1 20); do visitor -f -o /dev/null "https://$DOMAIN/healthz" 2>/dev/null && break; sleep 1; done

phase "7. HTTPS through Caddy, as a visitor ($CLIENT_IP)"
visitor -f -o /dev/null "https://$DOMAIN/healthz" || fail "https://$DOMAIN/healthz"
headers="$(visitor -D - -o /dev/null "https://$DOMAIN/")"
grep -qi "^content-security-policy: default-src 'none'" <<< "$headers" || fail "CSP"
grep -qi "^strict-transport-security:" <<< "$headers" || fail "HSTS"
grep -qiE "^(server|via):" <<< "$headers" && fail "Server or Via header"
pass "certificate verifies against Caddy's CA; CSP + HSTS; no Server/Via header"
[[ "$(visitor -o /dev/null -w '%{http_code} %{redirect_url}' "http://$DOMAIN/x")" == 30[18]\ "https://$DOMAIN/x" ]] \
  || fail "HTTP -> HTTPS"
[[ "$(visitor -o /dev/null -w '%{http_code} %{redirect_url}' "https://www.$DOMAIN/y")" == 30[18]\ "https://$DOMAIN/y" ]] \
  || fail "www redirect"
pass "http:// -> https://, www.$DOMAIN -> $DOMAIN"
jar=/tmp/jar
csrf_and_login="
  csrf=\$(curl -sS --cacert /ca.crt --resolve $DOMAIN:443:$VPS_IP -c $jar https://$DOMAIN/admin/login | sed -nE 's/.*name=\"csrf\" value=\"([^\"]+)\".*/\\1/p' | head -1)
  curl -sS --cacert /ca.crt --resolve $DOMAIN:443:$VPS_IP -b $jar -c $jar -H 'X-Forwarded-For: 6.6.6.6' -o /dev/null \
    --data-urlencode csrf=\$csrf --data-urlencode password=\$1 https://$DOMAIN/admin/login
  curl -sS --cacert /ca.crt --resolve $DOMAIN:443:$VPS_IP -b $jar https://$DOMAIN/admin/ | grep -c 'AH login status'"
docker run --rm --network "$NET" --ip "$CLIENT_IP" -v "$SIM/caddy-root.crt:/ca.crt:ro" --entrypoint sh "$CURL_IMAGE" \
  -c "$csrf_and_login" sh wrong-password >/dev/null || true
logline="$(vps docker logs ah-dashboard 2>&1 | grep 'failed admin login from' | tail -1)"
[[ "${logline##* from }" == "$CLIENT_IP" ]] || fail "the app saw '${logline##* from }', not the visitor $CLIENT_IP"
pass "a faked X-Forwarded-For is ignored: the app sees the visitor's real address ($CLIENT_IP)"
n="$(docker run --rm --network "$NET" --ip 172.30.99.51 -v "$SIM/caddy-root.crt:/ca.crt:ro" --entrypoint sh "$CURL_IMAGE" \
  -c "$csrf_and_login" sh "$ADMIN_PASSWORD")"
[[ "$n" -ge 1 ]] || fail "admin login over HTTPS"
pass "admin login with the password from configure works over HTTPS (__Host- cookie)"

# ---- 8. upgrade, broken release, rollback -------------------------------------------------------------
phase "8. Release v2 (upgrade), a broken release (automatic rollback), ah-iac rollback"
V2="$(release v2)"
ci_ssh "deploy $V2" > "$SIM/deploy2.log" 2>&1 || { cat "$SIM/deploy2.log"; fail "deploy v2"; }
status="$(ci_ssh status)"
if ! grep -qF "current:     $V2" <<< "$status" || ! grep -qF "previous:    $V1" <<< "$status"; then
  echo "$status"; fail "status after v2"
fi
[[ "$(vps sh -c 'ls /var/backups/ah-dashboard/*.tar.gz | wc -l')" -ge 1 ]] || fail "no backup before the upgrade"
pass "v2 deployed; previous = v1; the database was backed up first"
BROKEN="$(release broken '["python", "-c", "raise SystemExit(\"broken on purpose\")"]')"
if ci_ssh "deploy $BROKEN" > "$SIM/deploy3.log" 2>&1; then fail "the broken release reported success"; fi
grep -q "rolled back" "$SIM/deploy3.log" || { cat "$SIM/deploy3.log"; fail "no automatic rollback"; }
[[ "$(app_health)" == healthy ]] || fail "app unhealthy after the broken release"
visitor -f -o /dev/null "https://$DOMAIN/healthz" || fail "site down after the broken release"
ci_ssh status | has -F "current:     $V2" || fail "not back on v2"
pass "broken release: refused by the health check, back on v2 automatically, site stayed up"
"$ROOT/deploy/ah-iac" rollback > "$SIM/rollback.log" 2>&1 || { strip < "$SIM/rollback.log" | tail -20; fail "ah-iac rollback"; }
ci_ssh status | has -F "current:     $V1" || fail "rollback did not reach v1"
visitor -f -o /dev/null "https://$DOMAIN/healthz" || fail "site down after rollback"
pass "deploy/ah-iac rollback (Ansible, admin key): back on v1, site up"

phase "9. The CI key can do nothing else"
for bad in "id" "bash -i" "deploy docker.io/evil/x@sha256:$(printf 'a%.0s' {1..64})" "deploy $IMAGE_REPO:latest" "backup"; do
  if ci_ssh "$bad" >/dev/null 2>&1; then fail "the CI key could run: $bad"; fi
done
if docker run --rm --network "$NET" -v "$SIM/home/deploy_key:/k:ro" -v "$ROOT/$INV/known_hosts:/kh:ro" ah-iac-tools \
     ssh -i /k -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile=/kh "root@$VPS" true >/dev/null 2>&1; then
  fail "the CI key logs in as root"
fi
pass "shell commands, foreign images, tags, other commands and root login are all refused"

# ---- 10. change a setting --------------------------------------------------------------------------------
phase "10. Change a setting in the inventory, provision: the app picks it up"
before="$(vps docker inspect -f '{{.State.StartedAt}}' ah-dashboard)"
in_toolbox sed -i 's/PRIVACY_CONTACT: privacy@example.nl/PRIVACY_CONTACT: changed@example.nl/' "$INV/group_vars/all/vars.yml"
"$ROOT/deploy/ah-iac" provision > "$SIM/provision3.log" 2>&1 || { strip < "$SIM/provision3.log" | tail -30; fail "provision (change)"; }
[[ "$(vps docker inspect -f '{{.State.StartedAt}}' ah-dashboard)" != "$before" ]] || fail "app not restarted"
visitor -f "https://$DOMAIN/privacy" | has "changed@example.nl" || fail "new privacy contact not shown"
ci_ssh status | has -F "current:     $V1" || fail "a settings change switched the image"
pass "PRIVACY_CONTACT changed -> app.env updated -> app restarted on the same image -> /privacy shows it"

# ---- 11. reboot ------------------------------------------------------------------------------------------
phase "11. Reboot the VPS"
docker restart -t 30 "$VPS" >/dev/null
for _ in $(seq 1 60); do [[ "$(app_health)" == healthy ]] && break; sleep 2; done
[[ "$(app_health)" == healthy ]] || fail "app not back after reboot"
for _ in $(seq 1 20); do visitor -f -o /dev/null "https://$DOMAIN/healthz" 2>/dev/null && break; sleep 1; done
visitor -f -o /dev/null "https://$DOMAIN/healthz" || fail "site not back after reboot"
vps ufw status | has "Status: active" || fail "firewall off after reboot"
pass "after a reboot: Docker, Caddy, the app and the firewall come back by themselves"

phase "Done"
echo "All VPS simulation checks passed in $((SECONDS - t0))s."
