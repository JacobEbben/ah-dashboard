# Deployment (Infrastructure as Code)

Everything needed to run AH Dashboard on a VPS: server setup, Docker, Caddy with
automatic HTTPS certificates, the app, and automatic deploys from GitHub.

```
 your machine                         GitHub                                  VPS (Debian/Ubuntu)
 ────────────                         ──────                                  ───────────────────
 deploy/ah-iac configure  ──▶ inventory + encrypted vault (committed)
 deploy/ah-iac provision  ──────────────────────────── Ansible over SSH ──▶  firewall, SSH hardening,
                                                                               auto security updates,
                                                                               Docker, Caddy, app, ah-deploy
 git push main ─────────▶  Release workflow:
                           checks → build image → smoke test
                           → Trivy scan → push to GHCR
                           → ssh ah-deploy@vps "deploy <image@digest>" ──▶  ah-deploy: stop old, back up DB,
                                                                               start new, health check,
                                                                               automatic rollback
 Internet ──── 80/443 ──▶ Caddy (Let's Encrypt) ──── edge network ──▶ app (no published port)
```

| Path | What |
|---|---|
| `ah-iac` | The one command you run (needs only bash + Docker) |
| `tools/` | Ansible toolbox image, `configure.py` (asks for all values), `github_settings.py` |
| `ansible/site.yml` | Whole server: roles `base`, `docker`, `caddy`, `app` |
| `ansible/deploy.yml`, `ops.yml` | One deploy; status / rollback / backup / restart |
| `ansible/roles/app/files/ah-deploy` | The server-side deploy script (backup, health, rollback) |
| `ansible/inventory/production/` | Your values (`vars.yml`), secrets (`vault.yml`, encrypted), host keys |
| `tests/` | `run.sh` (all deployment tests), `smoke-image.sh` (used by CI) |
| `../.github/workflows/` | `ci.yml` (checks), `release.yml` (build + deploy), `infra.yml` (server setup from GitHub) |

## First-time setup

**Requirements:** a VPS with Debian 12/13 or Ubuntu 22.04/24.04 that you can SSH into (root or a sudo
user), a domain or subdomain, and a GitHub repository for this project.

1. **DNS.** Point an `A` record (and `AAAA` if the VPS has IPv6) for your subdomain, e.g.
   `ah.example.nl`, to the VPS. Let's Encrypt checks this, so do it first.

2. **Configure.** From the project folder:

   ```bash
   deploy/ah-iac configure
   ```

   It asks for the server, the domain, the certificate mode, the image repository, app settings
   and the secrets (admin password, optional Google client secret, ...), checks the DNS, and shows
   the server's SSH host keys for you to confirm. It also creates, outside the repository:
   - `~/.config/ah-dashboard/vault-pass`: the password of the encrypted vault. **Store a copy in
     your password manager**; without it the secrets can't be read.
   - `~/.config/ah-dashboard/deploy_key`: the key GitHub uses to deploy.

   Your SSH key for Ansible is `~/.ssh/id_ed25519` (set `AH_SSH_KEY` for another one). Run
   `configure` again at any time; Enter keeps every current value.

3. **Provision the server:**

   ```bash
   deploy/ah-iac provision --check --diff    # dry run: shows what would change
   deploy/ah-iac provision
   ```

   This installs the firewall (SSH, 80, 443 only), SSH hardening (keys only), automatic security
   updates, Docker, Caddy (which gets the certificate), the app's settings and `ah-deploy`. A
   brand-new VPS that still only allows password login: add `-k` the first time (asks for the
   SSH password); afterwards only keys work. Safe to run again: it only changes what differs.

4. **GitHub.** Push the repository, then:

   ```bash
   deploy/ah-iac github
   ```

   prints the environments, variables and secrets to create (with ready-to-run `gh` commands).
   Commit `deploy/ansible/inventory/` (the vault is encrypted) so the workflows can read the
   server's host keys.

5. **First release.** Push to `main` (or run the Release workflow by hand). It builds, tests,
   scans and pushes the image to GHCR, then deploys it. The first image is private on GHCR:
   either make the package public (it contains no secrets), or answer "private" in `configure`
   and give a token with only `read:packages`.

6. **AH login.** Open `https://<domain>/admin`, sign in with the admin password, and use
   "Log in with an AH login code".

## Certificates

Caddy gets and renews certificates by itself (`tls_mode` in `vars.yml`, set by `configure`):

| `tls_mode` | Certificates | Use for |
|---|---|---|
| `letsencrypt` | Real Let's Encrypt certificates, renewed automatically | Production |
| `letsencrypt-staging` | Let's Encrypt's test CA: browsers warn, but no rate limits | A first try: switch to `letsencrypt` once it works |
| `internal` | Caddy's own CA (no public DNS needed; browsers warn) | Test servers, private networks |

Also set: `acme_email` (Let's Encrypt account: expiry notices) and `redirect_www` (serve
`www.<domain>` as a redirect; needs its own DNS record). Certificates and the ACME account are
stored in the Docker volume `caddy-data`: keep it, because requesting new certificates too often
hits Let's Encrypt's rate limits. Changing these values and running `provision` reloads Caddy
without downtime; every Caddyfile is checked with `caddy validate` before it replaces the old one.

## Day to day

| What | How |
|---|---|
| Deploy | Push to `main` (Release workflow). By hand: `deploy/ah-iac deploy ghcr.io/<owner>/ah-dashboard@sha256:<digest>` |
| See what runs | `deploy/ah-iac status` |
| Go back one version | `deploy/ah-iac rollback` (or `ssh ah-deploy@<server> rollback` with the deploy key) |
| Back up the database now | `deploy/ah-iac backup` (stops the app for a few seconds) |
| Change a setting | Edit `inventory/production/group_vars/all/vars.yml`, or `ah-iac configure`; then `ah-iac provision` (restarts the app when its settings changed) |
| Change a secret | `deploy/ah-iac vault-edit`, then `ah-iac provision` |
| Server setup from GitHub | Actions → Infrastructure → `dry-run` or `apply` |

## How a deploy works (and why)

`ah-deploy` on the server is the only thing that changes the app container:

1. **Only images from your repository, by digest.** `<image_repo>@sha256:<64 hex>`; tags and other
   repositories are refused. The CI key can only run `deploy`, `status` and `rollback`
   (`authorized_keys`: `restrict,command="/usr/local/bin/ah-deploy-ssh"`).
2. **Stop first, then start.** There's never a second copy of the app: AH replaces the refresh
   token on every renewal, so two copies would invalidate each other's login. A deploy takes a few
   seconds of downtime; that's the price of one process.
3. **Back up before every change.** The database (`ah-instance` volume) goes to
   `/var/backups/ah-dashboard` (newest 10 kept) while the app is stopped.
4. **Health check, automatic rollback.** If the new image isn't healthy (Docker's `/healthz`
   check; a crash loop fails at once), the previous image is started again with the current
   database.
5. **Database versions only go up.** If the older image refuses the database (the new one had
   upgraded it), the backup taken before the upgrade is restored. Changes since then are lost, and
   if AH renewed its token in that time, log in to AH again in `/admin`.

The token seal secret (which encrypts the AH login in the database) lives in the vault, not in
the data volume, so a database backup alone can't decrypt the AH login. **Never change it**:
`configure` creates it once and keeps it.

## Security model

- **Two keys, two powers.** The CI deploy key (GitHub environment `production`) can only deploy
  images from your repository or roll back. The Ansible key (your own, or the `infrastructure`
  environment) can change the whole server: give that environment required reviewers.
- **Secrets** exist in three places only: the encrypted vault in the repository, `app.env` on the
  server (mode 600, root), and the running container's environment. Nothing secret is in the image
  or in CI logs (`no_log`).
- **Network:** the app publishes no port; only containers on the `edge` network (Caddy) reach it,
  which makes `TRUSTED_PROXY=*` safe. Caddy replaces any `X-Forwarded-For` a client sends, so
  rate limits and lockouts see each visitor (the network has IPv6, so IPv6 visitors keep their own
  addresses too).
- **Host keys** are pinned in `inventory/production/known_hosts` (confirmed by you in
  `configure`): Ansible and CI refuse a server whose key changed.

## Tests

```bash
deploy/ah-iac check            # or: bash deploy/tests/run.sh [--quick]
```

No server needed, never contacts AH. Ansible syntax and `ansible-lint`; every template rendered
with an example inventory and checked (`caddy validate`, `docker compose config`, the forced
settings in `app.env`); `shellcheck`; and a functional run of `ah-deploy` on local Docker
(everything named `ahtest-*`, a local registry): first deploy, refused images, upgrade with
backup, broken image with automatic rollback, rollback with database restore, restart, backup,
locking, the SSH forced command, and Caddy in front (HTTPS, redirects, headers, compression,
`X-Forwarded-For` spoofing). CI runs the same (`deploy-code` job).

### Rehearsal on a simulated VPS

```bash
bash deploy/tests/vps-sim.sh            # ~8 minutes, ~1.5 GB disk, removes everything afterwards
KEEP=1 bash deploy/tests/vps-sim.sh     # leave it running: https://ah.sim (add "<VPS IP> ah.sim" to /etc/hosts)
```

A fresh Debian 12 server as a privileged systemd container, set up by the real tools in the real
order: `ah-iac configure` → `ah-iac provision` (twice; the second must report `changed=0`) → the
Release workflow's build steps → deploy over the restricted CI key → HTTPS through Caddy as a
visitor → upgrade, broken release (automatic rollback), `ah-iac rollback` → CI key restrictions →
a settings change through the inventory → a reboot. Differences from a real VPS: Caddy uses
`tls_mode: internal` (Let's Encrypt can't validate a simulated domain) and a local TLS registry
plays GHCR. Run it after changing the roles; `provision --check --diff` is still the first step on
the real server.

## Updating the pinned versions

Dependabot proposes updates for GitHub Actions, both Dockerfiles' base images and the Python
packages. By hand:

- **Caddy:** `docker pull caddy:<version>-alpine`, put the digest from `docker image inspect` in
  `ansible/roles/caddy/defaults/main.yml`, run `ah-iac check`, then `ah-iac provision`.
- **Toolbox Python packages:** edit `tools/requirements.in`, then
  `docker run --rm -v "$PWD/deploy/tools:/w" -w /w python:3.12-slim sh -c "pip install -q pip-tools==7.7.0 && pip-compile -q --generate-hashes --strip-extras --allow-unsafe --no-header --output-file=requirements.lock requirements.in"`.
- **Ansible collections:** `ansible/requirements.yml`.
