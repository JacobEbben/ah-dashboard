# CLAUDE.md — AH Dashboard

Flask app (Waitress, one process) with three views on Albert Heijn offers:
- **Bargains**: per-store "Laatste Kans Koopjes", from GraphQL `bargainItems`.
- **Bonus**: this week's national Bonus, from REST `/mobile-services/bonuspage/...` plus GraphQL `bonusPromotions` for group deals.
- **Cart**: browser-only (no server API): bargains and Bonus deals picked with a card's cart button, checked against the latest results of the other two views.

The server calls AH with ONE operator-configured AH login; visitors need no account. Optional sign-in through the central login service (an OpenID Connect provider, e.g. the companion Login Service) only syncs settings. Also included: `/admin` panel, `/privacy` (export/delete), `/voorwaarden` (terms of use), `/mcp` (read-only MCP server). Python 3.12; deps Flask 3, waitress 3, cryptography (`requirements.txt`, pinned with hashes in `requirements.lock`).

## Commands (Docker only; no local venv/Node)

Checks run in throwaway containers with the repo mounted read-only. `ci/check.sh` is what CI runs; all of these must stay green (520 pytest tests + ruff + mypy; 55 node tests at time of writing).

```bash
docker compose up -d --build                                            # run on 127.0.0.1:8733, settings from .env
docker compose run --rm ah-dashboard python -m ah_dashboard.admin       # prints ADMIN_PASSWORD_HASH='...'
docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh ci/check.sh python  # pytest, ruff, mypy
docker run --rm -v "$PWD:/src:ro" -w /src node:22-slim sh ci/check.sh js        # tsc -p jsconfig.json + node --test tests/js
docker run --rm -v "$PWD:/src:ro" -w /src python:3.12-slim sh ci/check.sh lock  # requirements.lock == pip-compile(requirements.txt)
docker build -t ah-dashboard:ci . && bash deploy/smoke-image.sh ah-dashboard:ci   # what release.yml checks on the image
```

- Single test: append `-k name` / a path to `python -m pytest -q` (in the `ci/check.sh python` container, after `pip install -r requirements-dev.txt`).
- Lock regeneration: pip-tools 7.7.0, `pip-compile --generate-hashes --strip-extras --allow-unsafe --no-header --output-file=requirements.lock requirements.txt`. The container needs a writable mount for this.
- Tool config:
  - `pyproject.toml`: ruff line 120, rules F/E/W/I/B/UP/SIM/RUF; mypy `packages=["ah_dashboard"]`, `check_untyped_defs`; pytest `testpaths=["tests"]`, no cache.
  - `jsconfig.json`: checkJs, non-strict.
  - `package.json`: documents typescript 5.6.3 only; it is never installed into the image.
- The image (`Dockerfile`, digest-pinned `python:3.12-slim`) copies only `ah_dashboard/` and `serve.py`; it runs as uid 10001 `app` with `CMD python serve.py`.
- The project folder may be a VirtualBox shared folder: avoid symlinks (no `npm install` in it). Containers that write into it may need to run as root.

## Architecture

### Python layers
Dependencies point down only. `tests/test_architecture.py::RULES` enforces this:

```
blueprints api/ web/ admin/ mcp/  →  auth/  →  services/  →  ah/  →  domain/
```

- `domain/` imports no Flask, `ah`, `services`, `auth`, `container` or `db`. `ah/` imports no Flask/services/auth/container. `services/` imports no Flask/auth/container/blueprints. `auth/` imports no `services`/`ah`/blueprints.
- Only `ah/` may contain GraphQL documents (`ah/queries.py`) or `/mobile-services/` paths (`ah/gateway.py`). A test greps for this.
- `services/bargains.LoadGuard` uses the literal `"*"` for `ratelimit.SHARED` because services must not import Flask code.

### Composition root
`ah_dashboard/__init__.py:create_app(config=None, overrides=None)` builds the app in this order:
1. `load_config` (profile → env → overrides, then `validate`).
2. `Settings.from_config`.
3. Logging.
4. Seal secret and cookie settings.
5. One `container.Services`.
6. Scheduler jobs (`bonus` every 60 s when `BONUS_PREWARM`; `hourly-cleanup` = `sessions.prune()` + `db.checkpoint()`).
7. Blueprints (`/`, `/api`, `/admin`, `/mcp`), `errors.register`, `headers.register`.

### Reaching services and settings
- Code reaches services via `container.services()` (`services(app)` in tests).
- Read settings from the typed groups `services().settings.{ah,cache,limits,auth,app}`, not from `app.config`.
- `Services` has `settings, db, users, sessions, tokens, ah, scheduler, rate_limiter, admin_limiter, login_connection, login (property; tests may assign it), known_stores, bargains_cache, stores_cache, bonus_cache`.
- Shared logic lives on `Services` and is used by both `/api` and `/mcp`: `gateway` (property), `load_bonus_week()`, `bonus_week()`, `find_stores(postcode)`, `bargains(stores, client)` (`client=None` = admin force refresh, unbudgeted), `refresh_bonus_week()` (admin force refresh of the Bonus, `POST /api/bonus/force`; the header's Force refresh button acts on the current view); next week (everyone; its force reload admins only): `bonus_next_week()`, `refresh_bonus_next_week()`, `bonus_next_period()` behind `/api/bonus/next`, `/api/bonus/next/status`, `/api/bonus/next/force`.

### Module map
- `domain/`
  - `models.py`: `Offer` is one shape for both views; `to_json()` is the API contract. Common keys `kind,id,title,brand,unitSize,image,category,priceNow,priceWas`; bargain keys `storeId,storeName,stock,discount,expires,markdownType`; bonus keys `dealType,mechanism,minQty,priceFrom,productCount,isGroup,products[{id,title,size}]`.
  - `pricing.py`: `bonus_price`, `deal_type`.
  - `dates.py`: `amsterdam_today`, Dutch DST computed without tzdata.
- `ah/`
  - `client.py`: urllib, `query()` POST + `get()` REST; no redirects; http/https handlers only; env proxies ignored; gzip handled.
  - `session.py`: `AHSession` adds the token; on an auth failure it renews once and retries; a semaphore caps requests at `AH_MAX_CONCURRENT`; a `Pacer` (token bucket) lets them start at `AH_RATE_PER_SECOND` with bursts of `AH_RATE_BURST`; after `AHThrottled` (HTTP 429, or 403 with AH's CDN "Access Denied" page, see `client.throttle_error`) every request fails at once for `AH_THROTTLE_PAUSE_SECONDS` without contacting AH and the page gets 503 `ah_busy`; keeps stats.
  - `auth.py`: `TokenManager`, `TokenStore` (Fernet blob `ah_tokens` in table `secrets`), `token_kind`, `login_code`, `clean_pasted`.
  - `gateway.py`: `AHGateway`, the only place with AH paths and parameters.
  - `queries.py`: the GraphQL documents.
  - `mapping.py`: AH JSON → domain; `guarded()`, `AHDataError`.
- `services/`
  - `bargains.py`: `fetch_bargains`, `LoadGuard`, `StoreRefused`, `cache_error`.
  - `bonus.py`: `load_bonus`, `WeekCache`, `BonusStore` (the week in table `bonus_week`, read back strictly via `domain/models.bonus_week_from_stored`), `prewarm_job`.
  - `stores.py`: `KnownStores`, `normalize_postcode`.
  - `cache.py`: `TTLCache`, single-flight, stale-while-revalidate, remembered errors.
- `auth/`
  - `users.py`: `UserStore`, `SYNC_KEYS`, `validate_prefs`.
  - `sessions.py`: `SessionStore`, `begin`/`end`/`listed`.
  - `access.py`: `current_user`, `require_user_api`.
  - `admin.py`: `admin_user`, `logged_in`, `require_admin`, `require_admin_api`, `touch`, `drop`, `is_last_admin`.
  - `login_client.py`: `LoginClient`, `Pending` (OpenID Connect client; the same code as File Share's).
  - `csrf.py`, `compare.py` (`same_secret`), `lockout.py` (`LoginLimiter`, `LockoutPolicy`, `login_capacity`, `password_check_slot`).
- Cross-cutting: `errors.py`, `headers.py`, `ratelimit.py`, `scheduler.py`, `db.py`, `sealing.py`, `logging_setup.py` (`log_error` → 10-hex ref), `errorlog.py` (admin lookup of refs).

### Browser code (`ah_dashboard/static/js/`)
Plain ES modules with no build step. `main.js` is the composition root. Allowed imports (`JS_ALLOWED` in `test_architecture.py`):

| folder | may import |
|---|---|
| `core/` (dom, storage, i18n, api, events, config, format, unit, offers, layout, cart) | nothing of the app |
| `lists/` (likes, blacklist, keyed, panel, stores, cart) | core |
| `ui/` (cards, density, theme, view, reorder, rename, presets, storebar) | core, lists |
| `sync/` (sync, merge) | core |
| `bargains/`, `bonus/`, `cart/` | core, lists, ui; **never each other** |

- Cross-module reactions go through `core/events.js`: `on(EV.x, fn)` / `emit(EV.x, detail)`. The events are `EV.lists, lang, cols, stores {reload}, inStore, refilter, settings, needBonus, bonusStatus {status, deals, items, complete}, bargains {items, storeIds}, cart`. A new reaction needs a new `EV` key; never call another feature's render function directly.
- Each view has one controller that re-renders: `bargains/index.js`, `bonus/index.js`, `cart/index.js`. Other modules emit events.
- DOM-free logic lives in `bargains/model.js`, `bonus/model.js`, `core/layout.js`, `core/unit.js`, `core/offers.js`, `core/format.js`, `core/cart.js` and `sync/merge.js` (`lists/stores.js` and `lists/cart.js` also run under node). Each is tested in `tests/js/` (`node:test`, no DOM). Put new pure logic there and add a test.
- Server config reaches the page as JSON in `<script type="application/json" id="app-config">` (`web/routes.index`): `maxStores, syncKeys, user, csrf, imageHosts` (`headers.image_host_list(CSP_IMAGE_HOSTS)`, the same list as the CSP's img-src). `core/config.js` reads it.

## Invariants (do not change)

- **Key-derivation labels** in `__init__.py`: `SESSION_KEY_LABEL = b"ah-bargains flask session v1"`, `TOKEN_STORE_LABEL = b"ah-bargains token store v1"`. They keep the old project name on purpose; changing them makes the stored AH login unreadable and signs everyone out.
- **The seal secret**: `TOKEN_SEAL_SECRET`, or a generated `instance/token_seal.secret`. It must never be stored in the database, and changing it has the same effect as changing the labels.
- **localStorage keys** in `static/js/core/storage.js` `KEYS` (`ah_settings`, `ah_instore`, `ah_presets`, `ah_preset_order`, `ah_stores`, `ah_lang`, `ah_view`, `ah_bonus_cat`, `ah_bonus_filters`, `ah_bonus_presets`, `ah_bonus_preset_order`, `ah_bonus_groups`, `ah_bonus_notice`, `ah_theme`, `ah_blacklist`, `ah_liked`, `ah_show_hidden`, `ah_cart`, `ah_cart_removed`, `ah_cart_min_disc`, `ah_cart_sort`, `ah_cart_store`, `ah_cols`, `*_folded`), plus `ah_sync` in `sync/sync.js`. Renaming one wipes users' settings. `theme-init.js` also reads `ah_theme`.
- **Cookie name**: `ahb_admin` / `__Host-ahb_admin` (when `ADMIN_COOKIE_SECURE`). Session keys: `sid`, `csrf`, `user`, `admin_login_at`, `admin_seen_at`, `admin_pw`, `login_pending`.
- **One server process / one instance per AH login.** AH rotates the refresh token on every renewal, so a second process or deployment invalidates the other's token. The server's deploy command must stop the old container before starting the new one. Rate limits, lockouts and caches are in memory.
- **No custom User-Agent towards AH GraphQL** (`AH_USER_AGENT` unset). A custom one made AH fail with "Subgraph errors redacted".
- **Database schema** (`db.py`):
  - A new table or index goes into `_SCHEMA` (`IF NOT EXISTS`), with no version bump.
  - A change to an existing table is a new `MIGRATIONS[n]` step plus a bump of `SCHEMA_VERSION` (now 4). `_SCHEMA` must describe the end state, because fresh databases are created from it directly.
  - Never lower `SCHEMA_VERSION`: a database with a higher `user_version` is refused (`RuntimeError`), and the deploy rollback relies on this.
  - `users.password_hash` is kept but always `''`.
- **The synced settings whitelist** has exactly one source: `auth/users.SYNC_KEYS`, which reaches the page as `syncKeys` (a test checks this). Per-device keys (`ah_cols`, folded states, `ah_bonus_notice`) are deliberately not synced.
- **`.env.example` holds no real values**. `test_security_fixes.test_env_example_holds_no_secrets` requires `AH_ACCESS_TOKEN`, `AH_REFRESH_TOKEN`, `ADMIN_PASSWORD_HASH` and `TOKEN_SEAL_SECRET` to be empty and rejects token- or hash-shaped values.
- **Adding a setting**: add a default to `config.BaseConfig`, a field in its `Settings` group (plus the `from_config` line), and an entry in `.env.example`.

## Security rules (tests enforce most)

- **CSP** (`headers.py`): `default-src 'none'`; script/style/font/connect `'self'`; img `'self' data:` + https hosts from `CSP_IMAGE_HOSTS`. `tests/test_headers.py` scans `templates/**/*.html` and `static/js/**/*.js`, so:
  - no `style="…"` attributes,
  - no `on…=` handlers,
  - no inline `<script>` bodies (except `type="application/json"`),
  - no CDNs.
  Setting `element.style.x` from JS is fine. Store colours are classes (`sc0`…`sc9`, `scx`). Fonts are self-hosted in `static/fonts/`.
- **Escape all data in the browser**: build HTML with the `html` tagged template from `core/dom.js` (only `SafeHtml` passes unescaped), or use `textContent`. Image and link URLs go through `core/offers.safeImageUrl` (https only). Cart entries (synced, so untrusted) keep an image only on an allowed host: `core/offers.allowedImageUrl(url, config.imageHosts)` (exact origin or a `https://*.` subdomain wildcard, default port only), in `core/cart.entriesFor`/`cleanEntry` and again when drawing.
- **AH data reaches the browser only through `ah/mapping.py`**:
  - It whitelists fields; validators never raise.
  - NaN/Infinity, non-ASCII digits, booleans, wrong shapes and out-of-range numbers all become `None`.
  - Services call mapping through `mapping.guarded()`, so anything unexpected becomes `AHDataError` (an `AHError`) and fails only that store, category or group.
- **Errors**:
  - `errors.register` maps `AHAuthError` → 503 `ah_auth_failed` and any other `AHError` → 502 `upstream_error`, centrally (routes let them propagate). Anything unexpected → 500 `internal_error`.
  - Users see generic text plus a `ref` from `log_error()`. Never return upstream or raw error text, cache state, data age or TTLs (`BargainsResult.to_json` deliberately has no cache info).
  - Never log tokens, the admin password or its hash, sign-in codes or ID tokens.
- **Secret comparison**: only via `auth/compare.same_secret` (`hmac.compare_digest` on bytes). `test_no_direct_compare_digest` forbids `compare_digest(` anywhere else in the package.
- **Endpoints that reach AH** need `@rate_limited("bargains"|"stores"|"bonus")`, `@ah_required` (503 `ah_not_configured`) and a cache.
  - Store loads (cache misses) go through `LoadGuard`. Unknown store ids also count against the `UNKNOWN_STORES_*` budgets; all loads count against `STORE_LOADS_*`.
  - Refusals (`store_unknown`, `store_busy`) are neither cached nor error-logged.
  - The admin force refresh is not budgeted.
- **Client key** for every limit: `ratelimit.client_key()` = IPv4 address, or IPv6 /64 (IPv4-mapped → IPv4). Never key on the raw IPv6 address.
  - Server-wide budgets use client `ratelimit.SHARED` (`"*"`), which is kept apart and never evicted.
  - Behind a proxy, client addresses are correct only with `TRUSTED_PROXY` (`serve.waitress_options`).
- **Sessions** (`auth/sessions.py`): sign in only via `sessions.begin()`, through `access.login_user` / `admin.start_user_session` / `admin.start_password_session`. `begin()` ends the old sign-in, lists a new `sid` and makes a new CSRF token. Never set session keys by hand. Logout = `sessions.end()`, which deletes the row so copied cookies die. At most 20 sign-ins per account.
- **CSRF** on every state change:
  - Forms post a `csrf` field (`csrf.check_form`).
  - JSON calls send `X-CSRF-Token` (`csrf.check_header`). `require_user_api` checks it for non-GET; `require_admin_api` always does.
- **Admin**:
  - The panel exists only if `ADMIN_PASSWORD_HASH` is set or an enabled admin user exists (`auth.admin.enabled`); otherwise `/admin/*` is a 404.
  - Admin rights of users are re-derived on every request (`admin_user()`), so they are not a session.
  - The admin-password session has idle (`ADMIN_SESSION_MINUTES`, refreshed by `touch()`) and absolute (`ADMIN_SESSION_MAX_HOURS`) limits, and dies when the hash changes (the `admin_pw` fingerprint).
  - Without an admin password the last admin can't be demoted, disabled or deleted (`is_last_admin`).
- **Password lockouts** (admin password only; users have no passwords):
  - `LockoutPolicy` applies per client and per account (`*admin-password*`).
  - `LoginLimiter` never forgets a locked key.
  - `login_capacity()` is a server-wide cap checked before hashing.
  - `password_check_slot()` allows at most 2 concurrent scrypt checks.
  - **Never spend `login_capacity` on cheap, anonymous actions**; it would become a lock-everyone-out switch. Sign-in starts have their own per-client bucket `login-signin`.
- **Sign-in through the login service** (`auth/login_client.py`, `web/login.py`):
  - The connection comes from the admin panel's "Login service" card (`auth/login_connection.py`, table `login_config`,
    secret sealed with `LOGIN_CONFIG_LABEL`), else from `LOGIN_ISSUER`/`LOGIN_CLIENT_ID`/`LOGIN_CLIENT_SECRET`. Only an
    admin-PASSWORD session may save/test/disconnect it (`password_session()`, else 403); it is tested before saving.
    Without a connection `services().login` is None and `/auth/login`, `/auth/callback`, `/auth/backchannel-logout`
    are 404. The CSP form-action is computed per response from the current issuer.
  - Code flow with PKCE, `state`, `nonce`; the ID token's RS256 signature is checked against the issuer's JWKS, plus
    iss, aud/azp, exp/iat, nonce; the callback checks the RFC 9207 `iss`. Endpoints must be on the issuer's origin.
  - Accounts match on `login_sub`; an account WITHOUT one (Google era, or made by the admin) links on the first
    sign-in with the same username (`preferred_username`). The admin's "Unlink" forgets it (and signs out everywhere).
  - Sessions store the login service's `sid`, ID token and `amr`. Admin rights need `otp` in `amr`
    (`REQUIRE_ADMIN_2FA`, default on). Logout also redirects to the login service's end-session endpoint (so the CSP
    form-action lists the issuer's origin). Back-channel logout tokens (jti replay cache, exp, events) end sessions by
    `sid`, or every session of the user by `sub`.
  - Google is gone: `users.email` and `users.google_sub` are kept (unused), like `password_hash`. Schema 4 added
    `users.login_sub` and the session columns, and ended the Google-era sign-ins.
- **MCP** (`mcp/`):
  - Stateless (POST only; GET/DELETE 405; notifications 202), anonymous and read-only. Never read the session cookie there, and never add a tool that changes data or reads user data.
  - A foreign `Origin` gets 403. Each message counts against `RATE_LIMIT_MCP`; each tool call also against `Tool.bucket` and `LoadGuard`.
  - Bad arguments are tool results with `isError`. AH failures give a generic tool error with a ref; unexpected errors give JSON-RPC -32603 with `data.ref`.
- **Privacy** (`web/privacy.py`, `templates/privacy.html` with NL and EN text):
  - If you change what is stored, logged, or sent to third parties, update both language versions.
  - The export never includes session ids or `session_version`.
  - Delete requires CSRF, the typed e-mail (else username) and a checkbox. It is refused for the last admin without an admin password, and cascades to `user_prefs` and `sessions`.
- **Terms of use** (`web/terms.py`, `templates/terms.html`, NL + EN; `/terms` redirects to `/voorwaarden`): when the text changes, update BOTH language versions and `TERMS_UPDATED` (the date shown). Keep it true to what the site does (e.g. unit prices are computed here, stock is indicative). Both statement pages use `static/js/statement.js`, which shows the visitor's language (Dutch without JS) and takes the title from `data-title-key`. The footer in `base.html` links both on every page. The terms promise the MIT licence (`LICENSE`, "AH Dashboard contributors") and name the font licence: keep `static/fonts/OFL.txt` next to the fonts (a test checks both).
- Never open or print `.env`, `instance/`, `logs/` or real tokens. Never put tokens in URLs, logs or responses.

## Conventions

- **i18n**: every UI string exists in both `en` and `nl` in `static/js/core/i18n.js` (`tests/js/core.test.js` checks the key sets match). `tests/test_i18n_keys.py` checks that every key the page asks for exists and every key is used (as a quoted string in JS, a `data-i18n*`/`data-title-key` attribute, or a Python string such as an API error code); keys built at run time need a prefix in its `DYNAMIC_PREFIXES`. Removing a feature means removing its texts. Templates use `data-i18n`, `data-i18n-title`, `data-i18n-ph`, `data-i18n-aria`. Product names and categories from AH are not translated.
- **localStorage**: only through `core/storage.js` (`storage.get/set/remove/getJSON/setJSON/setQuiet`); `test_all_storage_access_goes_through_storage_js` enforces this (`theme-init.js` excepted). `set`/`remove` notify the sync listeners only on real changes. Use `setQuiet` for bookkeeping that must not trigger an upload.
- **JS typing**: every JS file starts with `// @ts-check` (`theme-init.js` excepted, as a classic script) and uses JSDoc types.
- **Result grids**: build sections with `ui/cards.section()` and place them with `placeSections(grid, sections, cols, dense)`; `core/layout.packSections` decides bands and columns.
- **Card sizes** scale with `--cs`: write sizes in `app.css` as `calc(Npx * var(--cs))`. `ui/density.autoCols()` mirrors the CSS breakpoints; keep them in step.
- **Bars**: choices and tool rows are segmented bars (`.segmented` + `.segbtn`, 46px high, one row; wider than the room → they scroll sideways). A bar with a field (the presets' select/name + Update/Delete/Save, the postal-code search) is `.segmented.toolseg` with `.segfield` inputs; the main action is `.segbtn.segmain`, a destructive one `.segbtn.danger`. Use these instead of loose inputs with `.mini` buttons.
- **Theme**: all colours are tokens at the top of `static/css/app.css`: light (`:root`, the default) and dark (`:root[data-theme="dark"]`, only when chosen in the header menu). The system's light/dark setting is deliberately NOT followed: no `prefers-color-scheme`, no `matchMedia` (`tests/test_theme.py`). `ah_theme` stores `dark`, or nothing for light. Don't add raw hex values in component rules.
- **Python style**:
  - Use `from __future__ import annotations` and frozen dataclasses for settings and models.
  - Routes stay thin. API errors are `APIError(code, message, status)` with a stable `code`; the frontend translates known codes.
  - AH-specific facts live in comments next to the code (`ah/session._AUTH_SYMPTOMS`, `ah/mapping`, `domain/pricing`).

## AH API facts (from the code)

- **Auth-failure symptoms** (`ah/session.py`): HTTP 401; GraphQL errors containing "Cannot return null for non-nullable type", `SESSION_EXPIRED`, `UNAUTHENTICATED` or `invalid_token`. The session then renews once (respecting `AH_REFRESH_COOLDOWN`) and retries.
- **Token shapes** (`token_kind`): refresh = UUID; access = `<digits>_<…>`.
  - Renewal: `POST …/mobile-auth/v1/auth/token/refresh` `{clientId, refreshToken}`.
  - Login code: `POST …/mobile-auth/v1/auth/token` `{clientId, code}`.
  - AH answers 400 for a value that isn't a refresh token, and 401 for an expired or used one.
  - Auth calls send the iOS-app headers in `ah/auth.AUTH_HEADERS`.
- **Bargain prices** (`bargainPrice.priceNow/priceWas`) arrive as **text** ("1.09"); `mapping.money_text` converts them. Fakes should send text too.
- **Bonus prices**: the REST pages have no `currentPrice`. The price is computed from `priceBeforeBonus` + `discountLabels` (`domain/pricing.bonus_price`, half-up cents).
  - Group deals come without products; `bonusPromotions(id)` fills `products` (webshop ids, max `MAX_GROUP_PRODUCTS=200`). On the card, the "N products" line is a button that unfolds them as links to ah.nl (`bonus/index.js` `groupLine`; open lists survive re-renders).
  - A Bonus load is ~300 AH requests: keep it in `WeekCache`. The cache keeps a copy until `bonusEndDate` (Dutch date), retries partial loads after `BONUS_RETRY_SECONDS`, and keeps this week's copy on failure. Failed group lookups (`groups_failed`) also make a week partial. Next week: AH's metadata lists it as a second period a few days ahead (`mapping.bonus_periods`, `services/bonus.next_period`); `load_bonus(next_week=True)` asks the sections with `date` = its start. Its own `WeekCache(next_week=True)` (usable until that week starts) is stored in table `bonus_next_week`; `NextWeekStatus` checks the metadata at most every 10 min. The Bonus view shows everyone a "Deze week | Volgende week" switch (`#bonusWeekSeg`) once next week is published; next week never feeds the bargains' Bonus tags or the liked notice. The copy is stored in the database, so redeploys don't reload it from AH (AH's CDN blocked the login after the ~160-request burst of each redeploy).
- **Store search** accepts only a postal code (`StoresFilterInput.postalCode`, limit 25).
- appie-go (github.com/gwillem/appie-go) is cited in comments as the reference for endpoints.

## Testing without external services

- Tests must never contact AH or the login service. Use the testing profile: `create_app("testing")` / `TestingConfig`.
  - It doesn't read the environment, uses the fixed `TOKEN_SEAL_SECRET`, an in-memory DB (`DATABASE_FILE=""`), no error log, no background jobs, `BARGAINS_STALE_SECONDS=0`, and all rate limits 0.
- `tests/conftest.py`:
  - fixtures `app`, `client`, `fake_client`;
  - `FakeAHClient(responses, fail_stores, stale_token_stores)` handles `BargainItems`/`StoresSearch` only; Bonus tests bring their own fake with `get()`;
  - `configure(app, KEY=value)` rebuilds `settings` on a running app;
  - `as_admin(client)` returns CSRF headers;
  - `enable_login`, `login_sign_in`, `sign_in` (a fake login service: `tests/fake_op.py`, real RS256 tokens).
- Swap any service by assignment: `services(app).ah = fake`, `services(app).login = LoginClient(..., http=FakeOP().http)` (see `enable_login`).
- Fake tokens in tests must stay obviously fake (`.gitleaks.toml` has no allowlist).
- There is no browser/E2E harness in the repo. To look at the UI, serve `create_app(TestingConfig)` with a canned `services(app).ah` fake via Waitress, in a throwaway container. Never use the real `.env`.
- `deploy/smoke-image.sh` (run by `release.yml` on the built image) asserts CSP, no `Server` header, `/admin/` 404, `/api/bonus` 503, read-only code, user `app`; it never contacts AH.

## Deployment

- `compose.yaml` (local): 127.0.0.1:8733; read-only rootfs, `tmpfs /tmp`, `cap_drop: ALL`, `no-new-privileges`; volumes `ah-instance` (`/app/instance`: DB + seal secret, **must persist**) and `ah-logs`. Keep `ADMIN_PASSWORD_HASH` single-quoted in `.env` (Compose expands `$`).
- Production: the image behind an HTTPS reverse proxy (no published port), with the settings `APP_ENV=production`, `TRUSTED_PROXY=*`, `TRUSTED_PROXY_COUNT=1`, `ADMIN_COOKIE_SECURE=true`, `PUBLIC_BASE_URL=https://<domain>` (README "Deploy").
- `release.yml` (main): CI → build → `deploy/smoke-image.sh` → Trivy CRITICAL → GHCR push → if the repository variable `DEPLOY_ENABLED` is `true`, SSH with `DEPLOY_SSH_KEY` and run `deploy <IMAGE_REPO>@sha256:<digest>` (a job `if:` can't see environment variables; the connection values are in environment `production`). The server's deploy command backs up the volumes, stops then starts, waits for health and rolls back. `ci.yml` also runs `pip-audit` and gitleaks over the full history.
- Secrets live in the server's secret store, never in the repo; don't read them.

## Gotchas

- `config.from_env` never reads `DEBUG`/`TESTING`; use `APP_ENV`. Empty `DATABASE_FILE` in the env means the default path; only the testing profile gets in-memory.
- Env values may be quoted: `config.unquote` strips ONE matching pair of `'…'`/`"…"` (in `from_env` and `serve.waitress_options`), like Compose does. Keep `ADMIN_PASSWORD_HASH` single-quoted in `.env` (it contains `$`).
- `services().login` is `None` without the login-service settings: guard for it.
- `AHGateway.configured` treats fakes without a `configured` attribute as configured.
- The `TTLCache` stale-reload uses `scheduler.spawn` (a no-op after `scheduler.stop()`). In tests `BARGAINS_STALE_SECONDS=0`, so there are no background reloads.
- `KnownStores` is loaded from the `known_stores` table at start-up and capped at 20,000 ids.
- Bargains page, top to bottom: the store bar (`_store_strip.html`; its last part ＋ opens the Stores panel), the minimum-discount buttons (`#minDisc`), the `.todayseg` bar (toggles `#expToday` "Ends today", `#likedOnly` "Liked" and `#notLikedOnly` "Not liked" (Liked and Not liked exclude each other), remembered in `ah_settings` as `endsToday`/`liked`/`notLiked`) and the presets bar (`_quickbar.html`; its last part ＋ opens Filters at the new preset name), then the panel row: Filters (`_filters_panel.html`, a `makeFoldable` panel: group, sort, product types, presets; `#filterCount`), Stores, Liked & hidden. Remove filters (`#clearFilters`) is always shown, disabled when nothing filters, and leaves stores alone. There is no product search and no "Bonus only" filter (old `bonusOnly`/`likedOnly` settings are ignored; the Liked filter's setting is `liked`, so an old `likedOnly` can't switch it on). Without the Liked filter, liking only puts a product first among equals (`model.sorter`). Remove filters also turns off Ends today, Liked and Not liked. Bonus page, like the bargains: the week switch, the presets bar (`#bonusPresetBar`: "All types", saved category selections, ＋ opens its Filters at the new preset name) with the Liked pill, then the panel row: Filters (`_bonus_filters_panel.html`: category chips, presets), Liked & hidden. Every chosen category is shown at once, one section per category (AH's order), liked first (`bonus/model.sortOffers`). There is no Bonus search (removed; the Liked pill and categories filter it). Presets for both views come from `ui/presets.js` (`makePresets`); the Bonus has its own (`ah_bonus_presets`, `ah_bonus_preset_order`); `ah_bonus_filters` keeps `{liked, cats}`. Category groups are gone: old `ah_bonus_groups` become presets once (`bonus/model.groupsToPresets`) and are removed with `ah_bonus_cat`. There are no dialogs/sheets.
- "I'm in this store": tapping a store chip stores `ah_instore` `{id, until}` (per device, not synced; `lists/stores.inStoreUntil` = 2 h or midnight, `activeInStore` validates) and emits `EV.inStore`. The store list and this state live in `lists/stores.js` (the cart uses the list); the Stores panel and search stay in `bargains/stores.js`. The store bar (`#storeChips`, `ui/storebar.js`, a segmented bar like `#minDisc` and `#presetBar`) stays and highlights that store with a ✕ (tap again to leave); `filterOffers` gets `storeId` and returns `otherStores` for the summary.
- Cart: its store bar (`#cartStoreChips`) is drawn by the same `ui/storebar.renderStoreBar` as the bargains' bar, but its selection is the cart's OWN filter (`ah_cart_store`, per device, not synced, no expiry; `core/cart.activeCartStore` drops a store that is no longer followed), independent of "I'm in this store". Its ＋ (`#cartStoreManageBtn`, wired in `main.js`) calls `bargains/stores.openStoreSearch` (switches to the bargains view, opens the Stores panel). Entries (`core/cart.js`, stored by `lists/cart.js` in `ah_cart`, synced, merged like likes, max 300) are copies of the offer: a bargain per store (`b:<storeId>:<productKey>`; a card merged across stores adds one per store, `mergeAcrossStores` keeps each store's `id`), a Bonus deal once (`p:<productKey>`, with its week `from`/`until`). A bargain follows its product: each `EV.bargains` load adds an entry for every followed store where a product the cart holds as a bargain is now a bargain too (`core/cart.entriesAtNewStores` via `lists/cart.followBargains`, capped by MAX_CART room). Removing is per store (that store may have thrown the product away while AH still lists it): the row ✕ (`lists/cart.removeEntry`) removes that store's entry, the card button the stores the card shows. `entryStatus`: gone = past its date, or its store's bargains loaded without it (`EV.bargains` storeIds exclude failed stores), or a complete Bonus week (`EV.bonusStatus` `complete`) without it; unknown otherwise (never greyed); next week's deals are `upcoming`. Rows show the latest discount/price when still listed, plus (bargains still listed only) the latest `stock` as "N left" (never the copy from when it was added), and, when this week's Bonus also has the product (`EV.bonusStatus` `deals`, incl. group-deal members), a "BONUS · deal" tag next to the discount (`cartSections` `bonusDeals` → row `bonusDeal`), and, left of the discount, the bargain cards' coloured stock squares: row `stores` = every followed store where the product is a bargain now (`indexBargains` `byProduct`, any discount; Bonus rows too), its latest stock, in store order; with the cart's store filter only that store. The cart asks for this week's Bonus with `EV.needBonus` whenever it has items. Below its store bar, the "Add to cart" bar (`.cartaddseg`, a label like the Sort bar's): `#cartLikedToday` ("Liked today (n)") puts every liked, not hidden bargain ending today in the cart (with the cart's store filter only that store's; `core/cart.likedEndingOn` over the `EV.bargains` items, `lists/cart.addToCart`, which never toggles off); n counts those not in the cart yet, disabled at 0. Then the select `#cartMinDisc` (each option "N%+ (n)") and its ＋ `#cartBigDiscount`, which adds every not-hidden bargain with at least N% off, any end day (`core/cart.bargainsFrom`), same store filter, count and removed-today rules; N is picked per visitor in `#cartMinDisc` (`MIN_DISC_CHOICES` 40–90, default `BIG_DISCOUNT` 70, stored in `ah_cart_min_disc` only when not the default, synced; `core/cart.cartMinDiscount` validates; the select offers only the N whose step (N up to the next choice) has a not-hidden bargain of the cart's selected store, `core/cart.minDiscChoices`, each labelled with how many it would add, all before the bargains load, never empty; a saved N not on offer is used as the highest one below, `effectiveMinDiscount`, and kept). `#cartSortSeg` (Name | Discount | Quantity) orders each section's rows (`core/cart.cartSections` `sort`: gone last, then discount (`rowDiscount`; Bonus from its prices) or stock, most first, unknown last, by discount a bargain also on Bonus (`bonusDeal`) first among equals, then name; `ah_cart_sort`, synced, absent = name). Each row ends in a borderless action strip (`.cartacts`): like (`toggleLike` on the live offer when loaded, so a group deal likes its members) and ✕. What the visitor removes (row ✕ or the card button, not "Remove gone") is remembered per store key for that day in `ah_cart_removed` `{day, keys}` (synced, merged per day by `core/cart.mergeRemoved`, max `MAX_REMOVED`): "Liked today" (`lists/cart.canAdd`/`addToCart`) and `followBargains` skip exactly those keys (other stores still get the product); the next day, Empty cart, or re-adding it with a card's cart button (`withoutRemoved`, those keys) forgets them.
- Admin templates are server-rendered with no JS; admin POSTs redirect with `flash()` messages.
- `/api/*` responses and every HTML page are `Cache-Control: no-store` (pages carry the CSRF token and account in `app-config`); admin pages also `X-Robots-Tag: noindex, nofollow`. Static files use Flask's defaults (no `SEND_FILE_MAX_AGE_DEFAULT` is set).
