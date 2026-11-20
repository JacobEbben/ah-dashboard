#!/usr/bin/env bash
# Tests of the deployment code (deploy/ah-iac check; CI job "deploy-code").
# Needs bash and Docker; no server, never contacts AH.
#
#   deploy/tests/run.sh            everything
#   deploy/tests/run.sh --quick    without the functional deploy test (fast)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUICK=0; [[ "${1:-}" == --quick ]] && QUICK=1
OUT="$(mktemp -d)"
chmod 755 "$OUT"
CADDY_IMAGE="$(sed -nE 's/^caddy_image: "(.*)"$/\1/p' "$ROOT/deploy/ansible/roles/caddy/defaults/main.yml")"
cleanup() { docker run --rm -v "$OUT:/out" alpine:3.20 sh -c 'rm -rf /out/*' >/dev/null 2>&1 || true; rm -rf "$OUT"; }
trap cleanup EXIT

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

docker build -q -t ah-iac-tools -f "$ROOT/deploy/tools/Dockerfile" "$ROOT/deploy" >/dev/null

# The toolbox, with the repository READ-ONLY and the test inventory.
tools() {
  docker run --rm -v "$ROOT:/work:ro" -v "$OUT:/out" -w /work/deploy/ansible \
    -e OUT=/out -e ANSIBLE_HOME=/tmp/ansible -e ANSIBLE_LOCAL_TEMP=/tmp/ansible/tmp \
    -e ANSIBLE_INVENTORY=/work/deploy/tests/inventory/hosts.yml \
    ah-iac-tools "$@"
}

step "Ansible syntax"
for playbook in site.yml deploy.yml ops.yml; do
  tools ansible-playbook --syntax-check "$playbook" >/dev/null
  echo "ok  $playbook"
done

step "ansible-lint"
tools sh -c 'cp -r /work/deploy/ansible /tmp/lint && cd /tmp/lint && ansible-lint -q site.yml deploy.yml ops.yml roles'
echo "ok"

step "Render templates (test inventory; functional variant)"
tools ansible-playbook ../tests/render.yml >/dev/null
tools ansible-playbook ../tests/render.yml -e @../tests/functional-vars.yml -e render_dir=/out/functional >/dev/null
find "$OUT" -type f | sed "s#^$OUT/#  #" | sort

step "Rendered files"
for variant in main functional; do
  docker run --rm --network none -v "$OUT/$variant/caddy-Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
    || { echo "caddy validate failed ($variant):"; docker run --rm --network none \
         -v "$OUT/$variant/caddy-Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" \
         caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; exit 1; }
  echo "ok  caddy validate ($variant)"
  # Compose resolves app.env and ./conf next to the file: check a copy we own.
  check_dir="$(mktemp -d)"
  cp "$OUT/$variant"/* "$check_dir/" && cp "$check_dir/app-app.env" "$check_dir/app.env" && mkdir "$check_dir/conf"
  for f in app-compose.yaml caddy-compose.yaml; do
    (cd "$check_dir" && AH_IMAGE=example/app@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
       docker compose -f "$f" config -q)
    echo "ok  docker compose config ($variant/$f)"
  done
  rm -rf "$check_dir"
done
env_file="$OUT/main/app-app.env"
if ! grep -q "^ADMIN_COOKIE_SECURE=true$" "$env_file" || ! grep -q "^TRUSTED_PROXY=\*$" "$env_file" \
   || ! grep -q "^PUBLIC_BASE_URL=https://ah.example.nl$" "$env_file" \
   || ! grep -q "^ADMIN_PASSWORD_HASH='scrypt:" "$env_file" || grep -q "AH_REFRESH_TOKEN" "$env_file"; then
  echo "app.env is not as expected"; exit 1
fi
echo "ok  app.env: proxy + HTTPS settings forced, hash quoted, empty secrets left out"
grep -qE "^\s+ports:" "$OUT/main/app-compose.yaml" && { echo "the app publishes a port"; exit 1; }
echo "ok  the app publishes no port (only Caddy reaches it)"

step "shellcheck"
docker run --rm -v "$ROOT:/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 \
  deploy/ah-iac deploy/ansible/roles/app/files/ah-deploy deploy/ansible/roles/app/files/ah-deploy-ssh \
  deploy/tests/run.sh deploy/tests/deploy-flow.sh deploy/tests/smoke-image.sh ci/check.sh
echo "ok"

if (( QUICK )); then
  echo; echo "Quick checks passed (functional deploy test skipped)."
  exit 0
fi

step "Functional: ah-deploy, rollback, restore, SSH wrapper, Caddy"
bash "$ROOT/deploy/tests/deploy-flow.sh" "$OUT/functional"
echo; echo "All deployment tests passed."
