# AH Dashboard

A small Flask web app that shows two kinds of Albert Heijn (AH) offers:

- **Bargains** – the "Laatste Kans Koopjes" (last-chance markdowns near their best-before date) of the stores you pick, from AH's GraphQL `bargainItems`.
- **Bonus** – this week's national Bonus deals, from AH's REST bonus pages (plus a GraphQL `bonusPromotions` lookup for group deals).

The server talks to AH with **one AH login configured by the operator**. Visitors need no AH account and no token; the dashboard is open to everyone. Signing in (with Google) is optional and only syncs a visitor's settings between devices.

It also offers an admin panel (`/admin`), a privacy page with data export/delete (`/privacy`), terms of use (`/voorwaarden`), and a read-only MCP endpoint (`/mcp`) for AI assistants.

---

## Features

### Bargains view
- Find stores by Dutch postal code (`1234AB` or `1234`; AH's store search only accepts postal codes) and keep your own list of stores, each with its own colour, shown as chips at the top.
- **I'm in this store**: tap a store in the store bar to see only its bargains (it stays highlighted) (the count line says how many match at your other stores). It ends when tapped again, after 2 hours or at midnight, and is kept per device.
- Bargains of up to `MAX_STORES` (default 25) stores at once, loaded in parallel. One failing store doesn't fail the others; it shows a generic message plus an error reference.
- Minimum discount as one-tap buttons, and a Filters panel: group by, order by, product types, presets. "Remove filters" is always in the count line. These are always remembered. Liking a product doesn't filter: liked products come first among equals.
- Product types and **presets** (named product-type selections) that can be reordered by dragging or Alt+arrow keys and renamed in place (double-click or F2).
- An "Overview" per store (number of products, stock, liked, hidden, ending today, average discount, per discount level). Every number is clickable and shows exactly those items.
- A green alert bar for the special product "AH Groente & Fruit zakje", which AH lists as a bargain with a 0% discount (`bargains/model.isSpecial`).
- The same product at the same discount in several stores is shown as one card.
- Bargain cards show a "Bonus" tag when the product is in this week's Bonus.

### Bonus view
- One AH Bonus category at a time (pills), or your own **category groups**. You make a group by dragging a category pill onto another one. Groups can also be managed in a panel (rename, add, remove, delete; works with the keyboard).
- A **Liked** filter that shows every liked product on Bonus this week. A notice bar tells you when liked products are on Bonus.
- Offers in AH's own order, liked products first.
- Group deals ("Alle …") list the products they cover. Their "from" price is the cheapest product's bonus price.

### Both views
- Liked and hidden products, shared by both views. A product is identified by its AH webshop id, or by its title when it has no id. Liking or hiding a group deal applies to all its products.
- Unit prices (per kg / l / piece), worked out from AH's pack-size text. Ambiguous sizes give no unit price rather than a guess.
- Dutch and English UI, light theme by default with a dark theme to switch to (never automatic), cards-per-row slider (Auto or 1–8).

### Accounts, admin, privacy, MCP
- **Optional Google sign-in** (OpenID Connect, code flow with PKCE, scopes `openid email`). It syncs a whitelisted set of settings via `/api/prefs`. When two devices both changed settings, liked/hidden lists and presets are merged.
- **Admin panel**: the AH login (paste tokens, or log in with an AH login code that the server exchanges), renewing the token, managing dashboard users (set e-mail, promote/demote, enable/disable, delete), looking up error references, lifting login lockouts, and server health.
- **Terms of use** (`/voorwaarden`, Dutch and English): not affiliated with Albert Heijn, no guarantee of availability, correctness or completeness (the price in the store applies), no advice, liability, fair use, accounts, intellectual property (with a contact route for rights holders such as Albert Heijn), Dutch law and the competent Dutch court. The footer links to both legal pages on every page.
- **Privacy page** in Dutch and English. Signed-in users can see what is stored about them, download it as JSON, link a Google account (accounts without one), or delete their account.
- **MCP server** at `/mcp`, with the read-only tools `find_stores`, `get_bargains` and `get_bonus`.

---

## Quick start (local, Docker)

Everything runs in Docker; no local Python virtualenv or Node is needed.

```bash
cp .env.example .env            # then edit .env (see below)
docker compose run --rm ah-dashboard python -m ah_dashboard.admin   # optional: prints ADMIN_PASSWORD_HASH='...'
docker compose up -d --build
# open http://127.0.0.1:8733
```

- `compose.yaml` publishes the app on **127.0.0.1:8733** only. It runs the container read-only with all capabilities dropped and `no-new-privileges`, and uses two named volumes: `ah-instance` → `/app/instance` (SQLite database + generated seal secret; **must persist**) and `ah-logs` → `/app/logs` (`errors.log`).
- `python -m ah_dashboard.admin` asks for a password (at least 12 characters) and prints `ADMIN_PASSWORD_HASH='…'`. Keep the single quotes in `.env`: the hash contains `$`, which Docker Compose would otherwise expand.
- Health check: `GET /healthz` returns `{"status": "ok"}`. It uses no database and never calls AH. The image's `HEALTHCHECK` uses it.

### Giving the server its AH login

You can do this in either of two ways:

1. **Environment**: put `AH_REFRESH_TOKEN` (a UUID) and optionally `AH_ACCESS_TOKEN` (`<member number>_…`) in `.env`. AH issues a new refresh token on every renewal, so the app keeps the current pair **encrypted in the database** and reads the environment values only to seed it. Setting a *different* `AH_REFRESH_TOKEN` later replaces the stored pair.
2. **Admin panel** (`/admin`): either paste the tokens, or open AH's login page (the panel links to `https://login.ah.nl/login?client_id=appie-ios&response_type=code&redirect_uri=appie://login-exit`) and paste the resulting code or the whole `appie://login-exit?code=…` link. The server exchanges it at `POST /mobile-auth/v1/auth/token`. The panel refuses values that have the wrong shape (for example an access token pasted in the refresh-token field).

Without an AH login, every endpoint that would call AH answers **503 `ah_not_configured`**.

> **Run one instance per AH login.** AH rotates the refresh token on every renewal. A second process or a second deployment using the same login invalidates the other's token.

---

## Configuration

Settings come from environment variables; Compose passes `.env` in. `ah_dashboard/config.py` holds the defaults (`BaseConfig`), and `.env.example` documents them. The profile is chosen with `APP_ENV` = `production` (default), `development` (DEBUG, log level DEBUG) or `testing` (ignores the environment).

The environment is parsed like each default (one pair of surrounding quotes is removed first, as Docker Compose does): integers stay integers (an unparsable value falls back to the default), booleans accept `1/true/yes/on`, and empty text means "unset". The app refuses to start if `AH_GRAPHQL_URL`, `AH_AUTH_REFRESH_URL` or `AH_REST_URL` is not `https://`, if `PUBLIC_BASE_URL` has a path or is not http(s), or if only one of `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` is set.

**Server process** (read by `serve.py`, not by `config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `HOST` / `PORT` / `THREADS` | `127.0.0.1` / `8733` / `8` | Waitress. The image and compose set `HOST=0.0.0.0`. |
| `TRUSTED_PROXY` | unset | Proxy address, or `*` when only the proxy can reach the app. Waitress then takes the client address from `X-Forwarded-For`. Without it, every visitor behind a proxy shares one address for rate limits and lockouts. |
| `TRUSTED_PROXY_COUNT` | `1` | Number of proxies to trust. |

**AH login and API**

| Variable | Default | Meaning |
|---|---|---|
| `AH_REFRESH_TOKEN`, `AH_ACCESS_TOKEN` | unset | Seed for the server's AH login (see above). Only the refresh token is strictly needed. |
| `TOKEN_SEAL_SECRET` | generated | At least 16 characters. The encryption keys for the stored AH login and the session-cookie signing key are derived from it. When unset, one is generated once into `instance/token_seal.secret`. **Changing it makes the stored login unreadable and signs everyone out.** |
| `AH_REFRESH_COOLDOWN` | `300` | Minimum seconds between token renewals. |
| `AH_GRAPHQL_URL`, `AH_REST_URL`, `AH_AUTH_REFRESH_URL` | `https://api.ah.nl/…` | Must be https. |
| `AH_CLIENT_ID`, `AH_APPLICATION`, `AH_CLIENT_VERSION` | `appie-ios`, `AHWEBSHOP`, `9.28` | Client headers sent to AH. |
| `AH_USER_AGENT` | unset | **Leave unset.** A custom User-Agent made AH fail GraphQL queries ("Subgraph errors redacted"). |
| `AH_TIMEOUT` | `20` | Seconds per AH request. |
| `AH_MAX_CONCURRENT` | `8` | AH requests in flight for the whole server. |
| `AH_RATE_PER_SECOND` | `2` | AH requests that may start per second on average (whole server); `0` = no pacing. |
| `AH_RATE_BURST` | `10` | AH requests that may start at once before pacing applies. |
| `AH_THROTTLE_PAUSE_SECONDS` | `900` | After AH refuses us (HTTP 429 or 403 "Access Denied"), no AH requests at all for this long. |
| `MAX_STORES` / `MAX_WORKERS` | `25` / `6` | Stores per bargains request / parallel loads per request. |

**Caches and load budgets**

| Variable | Default | Meaning |
|---|---|---|
| `BARGAINS_CACHE_TTL` | `300` | Seconds a store's bargains are served from memory. |
| `BARGAINS_STALE_SECONDS` | `600` | After the TTL, the old copy is served this much longer while it reloads in the background. |
| `BARGAINS_CACHE_MAX_STORES` | `2000` | Cached stores. |
| `STORE_ERROR_TTL` | `120` | How long a failed store load is remembered (not used for AH-login failures). |
| `STORE_SEARCH_CACHE_TTL` | `86400` | Store search results per postal code. |
| `STORE_LOADS_PER_CLIENT` / `STORE_LOADS_TOTAL` | `40` / `120` | Store loads from AH per minute (cache misses only). |
| `UNKNOWN_STORES_PER_CLIENT` / `UNKNOWN_STORES_TOTAL` / `UNKNOWN_STORES_WINDOW` | `10` / `60` / `3600` | Loads of store ids not known to exist. |
| `BONUS_RETRY_SECONDS` | `300` | Retry delay for a partial or stale Bonus load. |
| `BONUS_PREWARM` | `true` | Load the Bonus in the background (at start-up and when the week changes). |
| `BACKGROUND_JOBS` | `true` | Run the scheduler (Bonus prewarm, hourly cleanup). |

**Rate limits** (per client key; `0` turns a limit off)

| Variable | Default |
|---|---|
| `RATE_LIMIT_BARGAINS` / `_SECONDS` | `30` / `60` |
| `RATE_LIMIT_STORES` / `_SECONDS` | `10` / `60` |
| `RATE_LIMIT_BONUS` / `_SECONDS` | `30` / `60` |
| `RATE_LIMIT_MCP` / `_SECONDS` | `120` / `60` |
| `GOOGLE_SIGNIN_PER_MINUTE` | `10` (per client) |
| `PREFS_WRITES_PER_MINUTE` | `60` (per signed-in user) |
| `LOGIN_ATTEMPTS_PER_MINUTE` | `60` (admin-password attempts, whole server) |

**Admin, users, cookies**

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_PASSWORD_HASH` | unset | werkzeug hash from `python -m ah_dashboard.admin`. The panel exists only when this is set **or** an admin user exists; otherwise `/admin` is a 404. |
| `ADMIN_SESSION_MINUTES` / `ADMIN_SESSION_MAX_HOURS` | `60` / `8` | Idle and absolute limits of an admin-*password* session. |
| `ADMIN_MAX_FAILURES` / `ADMIN_LOCKOUT_SECONDS` | `5` / `900` | Wrong admin passwords per client within the window. |
| `ACCOUNT_MAX_FAILURES` | `20` | Wrong admin passwords from any client within the window. |
| `ADMIN_COOKIE_SECURE` | `false` | HTTPS-only cookie named `__Host-ahb_admin` (else `ahb_admin`), plus HSTS. Set `true` behind HTTPS. Changing it renames the cookie, which signs everyone out once. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | unset | Google sign-in. Without them there is no sign-in button and `/auth/google` is a 404. |
| `PUBLIC_BASE_URL` | unset | e.g. `https://ah.example.nl`. Google returns to `<PUBLIC_BASE_URL>/auth/google/callback`, which must be listed in the OAuth client. When unset, the address is taken from the request. |
| `MAX_USERS` | `1000` | New Google accounts are refused beyond this. |
| `USER_SESSION_DAYS` | `30` | How long a sign-in lasts. |
| `DATABASE_FILE` | `instance/ah_dashboard.db` | SQLite file. |
| `PRIVACY_CONTACT` | unset | E-mail address or URL shown on `/privacy` and `/voorwaarden`. |

**Other**: `MCP_ENABLED` (`true`; `false` makes `/mcp` a 404), `CSP_IMAGE_HOSTS` (`https://static.ah.nl`; only `https://` entries are used), `MAX_CONTENT_LENGTH` (1 MiB), `LOG_LEVEL`, `ERROR_LOG_FILE` (`logs/errors.log`; empty turns it off), `ERROR_LOG_MAX_BYTES`, `ERROR_LOG_BACKUPS`.

---

## HTTP endpoints

| Path | Who | Notes |
|---|---|---|
| `GET /` | everyone | The page. Server settings are embedded as JSON in `<script id="app-config">`. |
| `GET /healthz` | everyone | Liveness check. |
| `GET /login`, `POST /logout` | everyone | `/login` goes straight to Google, unless there is an error to show or Google sign-in is off. |
| `GET /auth/google?purpose=login\|admin\|link`, `GET /auth/google/callback` | everyone | Google sign-in. |
| `GET /privacy`, `GET /privacy/export`, `POST /privacy/delete` | everyone / signed in | Privacy statement, data export, account deletion. |
| `GET /voorwaarden` (`/terms` redirects) | everyone | Terms of use. |
| `GET /api/stores?postcode=` | everyone | `{stores, postcode}`; rate-limited (`stores`). |
| `POST /api/bargains` | everyone | body `{"stores": [{"id", "name"}]}` → `{items, storeErrors}`; rate-limited and budgeted. |
| `POST /api/bargains/force` | admin + `X-CSRF-Token` | Reloads from AH now. Not budgeted. |
| `GET /api/bonus/next/status` | anyone (limit `RATE_LIMIT_BONUS`) | `{available, period}`: has AH published next week's Bonus (at most one AH request per 10 min). |
| `GET /api/bonus/next` | anyone (limit `RATE_LIMIT_BONUS`) | Next week's Bonus, shaped like `/api/bonus`; 404 `bonus_next_unavailable` until AH publishes it. Stored like this week's. |
| `POST /api/bonus/next/force` | admin + `X-CSRF-Token` | Reloads next week's Bonus from AH now. |
| `POST /api/bonus/force` | admin + `X-CSRF-Token` | Reloads this week's Bonus from AH now (~160 paced requests, a minute or two); a failed or worse load keeps the current copy. |
| `GET /api/bonus` | everyone | `{period, categories, items, errors}`. |
| `GET /api/session` | everyone | `{"admin": bool, "csrf"?}`. |
| `GET/PUT /api/prefs` | signed-in user (`PUT` needs `X-CSRF-Token`) | Synced settings. |
| `/admin/…` | admin | Server-rendered panel; every POST carries a CSRF form field. |
| `POST /mcp` | everyone | MCP (JSON-RPC 2.0, stateless). `GET`/`DELETE` → 405. |

API errors look like `{"error": {"code", "message", "ref"?}}`. Messages are generic. The `ref` matches an entry in `logs/errors.log`, and the admin panel can look it up (`/admin/errors?ref=`).

### MCP

`/mcp` implements MCP over Streamable HTTP, **stateless**: each `POST` carries one JSON-RPC message and gets one JSON answer. There is no `Mcp-Session-Id` and no SSE, and notifications get 202. Supported methods: `initialize`, `ping`, `tools/list`, `tools/call`. Protocol versions: `2025-11-25`, `2025-06-18`, `2025-03-26`.

| Tool | Arguments |
|---|---|
| `find_stores` | `postcode` |
| `get_bargains` | `store_ids` (required), `search`, `min_discount`, `category`, `sort` (`store\|discount\|price`), `limit` (1–200) |
| `get_bonus` | `search`, `category`, `deal_type`, `sort` (`category\|price`), `limit` |

The endpoint is anonymous and never reads the session cookie. A request from a browser with a foreign `Origin` gets 403. Every message counts against `RATE_LIMIT_MCP`, and every tool call also counts against its endpoint's limit and the store-load budgets.

---

## Security model

- **One shared AH login, protected against abuse.**
  - Every endpoint that can reach AH is rate-limited per client and cached.
  - Store loads from AH (cache misses) are budgeted per client and server-wide (`services/bargains.LoadGuard`).
  - Store ids that are not known to exist fall under a much smaller budget. A store becomes known when it appears in a store search or turns out to have bargains (table `known_stores`).
  - At most `AH_MAX_CONCURRENT` AH requests run at once, and they start at `AH_RATE_PER_SECOND` (bursts of `AH_RATE_BURST`): AH's CDN blocks the login after a quick burst of ~160 requests.
  - When AH refuses us anyway, all AH requests stop for `AH_THROTTLE_PAUSE_SECONDS`; visitors get "AH is busy" (`ah_busy`).
  - The Bonus week is also kept in the database, so a restart or redeploy doesn't load it from AH again.
  - Limits count per **client key**: the IPv4 address, or the IPv6 **/64** network (`ratelimit.limit_key`).
- **AH client hardening** (`ah/client.py`): only http/https handlers, redirects are never followed (the bearer token would leak), and proxy settings from the environment are ignored. The upstream URLs must be https.
- **Untrusted AH data**: every field from AH goes through `ah/mapping.py`, which whitelists and validates it. Odd values become `None`, and anything unexpected becomes `AHDataError`, which fails only that store or category.
- **Encryption at rest**: the AH token pair is stored Fernet-encrypted in the `secrets` table. The key is derived with HKDF from the seal secret, which is never stored in the database.
- **Strict headers** (`headers.py`):
  - CSP `default-src 'none'`; scripts, styles, fonts and connections from `'self'` only; images from `'self' data: https://static.ah.nl`; `frame-ancestors 'none'`.
  - Also nosniff, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a Permissions-Policy denying unused features, COOP/CORP `same-origin`, and HSTS when `ADMIN_COOKIE_SECURE` is on.
  - No `Server` header. API responses are `Cache-Control: no-store`.
  - Templates and JS contain no inline scripts, inline styles or `on…=` handlers (a test checks this).
- **Browser code**: HTML is built with the escaping `html` tagged template (`static/js/core/dom.js`). Only https image URLs are used.
- **Sessions**: one signed cookie (HttpOnly, SameSite=Lax). Every sign-in also gets a random id stored in the `sessions` table, so logging out kills copies of the cookie too. Every state-changing request needs the per-session CSRF token (form field `csrf` or header `X-CSRF-Token`). Tokens are compared with `auth/compare.same_secret` only.
- **Admin access**:
  - Admin users sign in with Google. Their rights are re-derived from the database on every request.
  - The admin password is the emergency way in. It has idle and absolute time limits and ends when the password hash changes.
  - Lockouts apply per client and per account, a server-wide cap applies before hashing, and at most 2 password checks run at once.
  - Without an admin password, the last admin user can't be demoted, disabled or deleted.
- **Google sign-in**:
  - State, nonce and the PKCE verifier are kept in the session for 10 minutes and used once.
  - The code is exchanged server-to-server. The ID token's `iss`, `aud`/`azp`, `exp`, `nonce` and `email_verified` are checked; its signature is not checked, because the token comes straight from Google's token endpoint over TLS.
  - Accounts are matched by Google `sub`. A linked Google account is never replaced by the user themselves.
  - No Google tokens are stored.
- **Errors**: users get generic messages with a reference; details go to the rotating error log. Tokens are never logged.

---

## Deployment

### Single host with Compose
`docker compose up -d --build` (see Quick start). To expose the app, put an HTTPS reverse proxy in front, and set `TRUSTED_PROXY`, `ADMIN_COOKIE_SECURE=true` and `PUBLIC_BASE_URL`.

### VPS with Caddy (Ansible, `deploy/`)
`deploy/ah-iac` needs only bash and Docker. Ansible runs in a toolbox image built from `deploy/tools/Dockerfile`.

```bash
deploy/ah-iac configure          # asks for server, domain, TLS mode, image repo, keys, settings, secrets
deploy/ah-iac provision          # site.yml: roles base, docker, caddy, app (idempotent)
deploy/ah-iac deploy ghcr.io/<owner>/ah-dashboard@sha256:<digest>
deploy/ah-iac status | rollback | backup | restart
deploy/ah-iac vault-edit | github | check | shell
```

- `configure` writes `deploy/ansible/inventory/<name>/` (`hosts.yml`, `group_vars/all/vars.yml`, the ansible-vault encrypted `vault.yml`, `known_hosts`). The vault password and the CI deploy key live outside the repository, in `$AH_IAC_HOME` (default `~/.config/ah-dashboard`).
- **base** role: Debian/Ubuntu only; unattended security upgrades; SSH key-only logins (refuses to run without an admin key); ufw (SSH, 80, 443 incl. UDP).
- **docker** role: Docker Engine with log rotation and `live-restore`; optional registry login.
- **caddy** role: digest-pinned Caddy on the `edge` network; `encode zstd gzip`; strips `Server`; TLS modes `letsencrypt`, `letsencrypt-staging`, `internal`; optional `www.` redirect.
- **app** role:
  - Writes `/opt/ah-dashboard/compose.yaml` (no published ports; the app is reachable only by Caddy on `edge`) and a root-only `app.env`.
  - `app.env` always contains `APP_ENV=production`, `TRUSTED_PROXY=*`, `ADMIN_COOKIE_SECURE=true` and `PUBLIC_BASE_URL=https://<domain>`. Secrets come from the vault, single-quoted.
  - Installs `/usr/local/bin/ah-deploy`, plus a restricted `ah-deploy` user whose SSH key may only run `ah-deploy-ssh` (`deploy`, `status`, `rollback`).
- **`ah-deploy deploy`**:
  - Accepts only `<image_repo>@sha256:<64 hex>`.
  - Stops the old container before starting the new one (one process).
  - Takes a tar backup of the instance volume to `/var/backups/ah-dashboard` (keeps 10).
  - Waits for the Docker health check.
  - If the new image is unhealthy, rolls back to the previous image, restoring the backup only if the older image refuses the database.

### CI/CD (`.github/workflows/`)
- `ci.yml` runs on PRs and non-main pushes, with these jobs:
  - Python (pytest, ruff, mypy)
  - JS (tsc + node tests)
  - `requirements.lock` in sync with `requirements.txt`
  - `pip-audit` of both locks
  - gitleaks over the whole history (`.gitleaks.toml`: default rules, no allowlist)
  - deployment-code tests (`deploy/tests/run.sh`)
- `release.yml` runs on push to main:
  1. The CI checks.
  2. Build the image.
  3. `deploy/tests/smoke-image.sh`.
  4. Trivy (fixable CRITICAL issues fail the release).
  5. Push to `ghcr.io/<owner>/ah-dashboard` (`sha-<sha>` and `main` tags).
  6. If the repository variable `DEPLOY_ENABLED` is `true`: deploy over SSH with the restricted key (environment `production`), then a public `/healthz` check.
- `infra.yml` is manual: runs `site.yml` as a dry run or apply (environment `infrastructure`).
- `dependabot.yml` sends weekly updates for GitHub Actions, Docker base digests, and pip (app and toolbox).

---

## Development and testing

There are no local toolchains. The checks run in throwaway containers with the project mounted read-only; `ci/check.sh` is the same script CI uses.

```bash
# Python: pytest, ruff, mypy
docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh ci/check.sh python
# Browser code: tsc (JSDoc + // @ts-check, jsconfig.json) and node:test unit tests (tests/js)
docker run --rm -v "$PWD:/src:ro" -w /src node:22-slim sh ci/check.sh js
# requirements.lock matches requirements.txt
docker run --rm -v "$PWD:/src:ro" -w /src python:3.12-slim sh ci/check.sh lock
# Deployment code (Ansible syntax/lint, template rendering, caddy validate, shellcheck, functional deploy flow)
bash deploy/tests/run.sh            # --quick skips the functional deploy test
bash deploy/tests/vps-sim.sh        # full rehearsal on a simulated Debian VPS (~10 min)
```

- **Regenerating `requirements.lock`** after changing `requirements.txt` uses pip-tools 7.7.0, with the flags from `ci/check.sh lock`: `pip-compile --generate-hashes --strip-extras --allow-unsafe --no-header --output-file=requirements.lock requirements.txt`. The container has to write into the project for this.
- **Tests never contact AH.**
  - `tests/conftest.py` provides `FakeAHClient`, which you assign to `services(app).ah`.
  - The `testing` profile has no tokens and doesn't read the environment. It uses an in-memory database, no error log, no background jobs, and rate limits off.
  - `configure(app, KEY=value)` changes settings of a running test app.
  - `as_admin(client)` gives a client an admin session.
  - `enable_google` / `google_sign_in` / `sign_in` use a fake Google token endpoint.
- The image contains no tests or dev tools (`.dockerignore` excludes `tests/`, `requirements-dev.txt`, `deploy/`, `.github/`, `ci/`).

---

## Project layout

```
serve.py                  Waitress entry point (container CMD); waitress_options()
ah_dashboard/
  __init__.py             create_app(): composition root
  config.py               profiles, env parsing, validate(), typed Settings
  container.py            Services dataclass + services(app)
  db.py                   SQLite (one locked connection, WAL, migrations)
  sealing.py              seal secret, HKDF/Fernet keys
  errors.py, headers.py, ratelimit.py, scheduler.py, logging_setup.py, errorlog.py
  domain/                 models (Offer, Store, BonusWeek), pricing, dates (no Flask, no I/O)
  ah/                     client (urllib), session (token + renewal), auth (TokenManager),
                          gateway (all AH endpoints), queries (GraphQL), mapping (validation)
  services/               bargains (LoadGuard), bonus (WeekCache), stores (KnownStores), cache (TTLCache)
  auth/                   users, sessions, access, admin, google, csrf, compare, lockout
  api/ web/ admin/ mcp/   Flask blueprints
  templates/              Jinja pages and partials
  static/js/              ES modules, no build step: core/ lists/ ui/ sync/ bargains/ bonus/ + main.js
  static/css/app.css      the whole theme; static/fonts/ self-hosted woff2
tests/                    pytest suite; tests/js/ node:test suite
ci/check.sh               checks used locally and in CI
deploy/                   ah-iac, Ansible roles/playbooks, toolbox, deployment tests
.github/                  workflows, dependabot
```

Database tables: `users`, `user_prefs`, `sessions`, `known_stores`, `secrets`. The schema version is kept in SQLite's `user_version` (currently 3), and the app refuses a database from a newer version.

## Licence

The code is released under the [MIT licence](LICENSE) (Copyright (c) 2026 AH Dashboard contributors).
The bundled fonts Red Hat Display and Red Hat Text (`ah_dashboard/static/fonts/`) are under the SIL Open
Font License 1.1 ([OFL.txt](ah_dashboard/static/fonts/OFL.txt)), which must stay next to them. Albert Heijn,
AH and Bonus are trademarks of Albert Heijn; this project is not affiliated with Albert Heijn.
