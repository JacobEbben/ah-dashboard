# Architecture

AH Dashboard is one Flask process that shows Albert Heijn offers in two views:
**Bargains** (last-chance markdowns per store) and **Bonus** (the weekly
national deals). The server calls AH with one configured AH login; visitors
need no account. This page describes how the code is organised and why.
Commands and day-to-day rules are in `CLAUDE.md`; operating the app is in `README.md`.

## Server layers

```
            HTTP
              │
┌─────────────▼──────────────┐   web/      the page, /login, /auth/login, /logout, /healthz
│ Blueprints (Flask)         │   api/      JSON API (/api)
│                            │   admin/    admin panel (/admin)
│                            │   mcp/      MCP server for AI assistants (/mcp)
└──┬───────────────┬─────────┘
   │               │
   │      ┌────────▼─────────┐   auth/     users, sign-in sessions, CSRF,
   │      │ auth             │             lockouts, admin access
   │      └────────┬─────────┘
┌──▼───────────────▼─────────┐   services/ orchestration: caching, budgets,
│ services                   │             parallel loading, the Bonus week
└──┬───────────────┬─────────┘
   │               │
┌──▼─────────┐  ┌──▼─────────┐   ah/       gateway (endpoints), mapping
│ ah         │  │ domain     │             (AH JSON → models), client, session,
│ (adapter)  ├─►│ (models)   │             token renewal
└──┬─────────┘  └────────────┘   domain/   Offer, Store, BonusWeek; pricing; dates
   │
   ▼  api.ah.nl (GraphQL + REST)
```

Cross-cutting modules: `config.py` (settings), `container.py` (services),
`db.py` (SQLite + migrations), `scheduler.py` (background work),
`errors.py` (error responses), `headers.py` (security headers),
`ratelimit.py` (per-client limits), `sealing.py` (keys), `logging_setup.py`.

**Dependency rule**: arrows point down only. `domain/` imports nothing of the
app; `ah/` never imports Flask or `services/`; `services/` never imports
Flask, `auth/` or the blueprints. `tests/test_architecture.py` checks this on
every test run, and also that only `ah/` knows AH's queries and paths.

### Composition root

`create_app()` (in `ah_dashboard/__init__.py`) is the only place that builds
objects:

1. `config.load_config()` builds the flat configuration: profile defaults →
   environment (read now, never at import; not for the testing profile) →
   overrides. It is validated (AH URLs must be https).
2. `Settings.from_config()` turns it into typed, grouped settings
   (`settings.ah`, `.cache`, `.limits`, `.auth`, `.app`). Code reads these,
   not `app.config`.
3. `_build_services()` creates every service once and puts them in one typed
   `Services` container (`app.extensions["services"]`, reached through
   `container.services()`).
4. Background jobs are registered on the `Scheduler`, blueprints and handlers
   are registered, and the scheduler starts (`BACKGROUND_JOBS`; off in tests).

Tests replace a part by assignment, e.g. `services(app).ah = FakeAH()`.

### The AH adapter

```
services ──► AHGateway ──► AHSession ──► AHClient ──► api.ah.nl
              │ endpoints     │ token, renewal,   │ urllib: no redirects,
              │ (queries,     │ retry once,       │ http/https only,
              │  paths)       │ concurrency cap,  │ gzip
              │               │ counters          │
              ▼
          ah.mapping: raw JSON ─► domain models (whitelisted, validated)
```

- `AHGateway` is the only place with GraphQL documents (`queries.py`), REST
  paths and request parameters. A method returns AH's raw JSON.
- `ah.mapping` is the anti-corruption layer: every field that reaches the
  browser is picked, type-checked and size-capped here. Nothing else parses
  AH's shapes.
- `AHSession` adds the server's token, renews it once on an auth failure and
  retries, caps requests in flight (`AH_MAX_CONCURRENT`) and counts requests
  for the admin panel.

### Domain models and the API contract

`domain/models.py` defines what the app knows, independent of AH:

- `Offer`: one shape for both views. Common fields (id, title, brand, unit
  size, image, category, priceNow, priceWas) plus a bargain part (store,
  stock, discount, expiry, markdown type) or a Bonus part (deal type,
  mechanism, min. quantity, "from" price, group products).
- `Store`, `StoreRef`, `BonusWeek` (period, categories, offers, failed
  categories).

`to_json()` of these models **is** the API contract; the browser's JSDoc
`Offer` type in `static/js/core/offers.js` describes the same shape. The
product `id` is AH's webshop id in both views, so liking or hiding works
across them.

`domain/pricing.py` calculates Bonus prices from AH's discount labels (AH
sends no current price on the bonus pages).

### Errors

Routes don't translate AH failures themselves. `errors.register()` maps them
once for every endpoint: `AHAuthError` → 503 `ah_auth_failed`, any other
`AHError` → 502 `upstream_error`, unexpected exceptions → 500. Every response
carries a generic message and a reference id; the details go to
`logs/errors.log` under that id.

### Caching and background work

| What | Where | Kept |
| --- | --- | --- |
| Bargains per store | `TTLCache` | `BARGAINS_CACHE_TTL`, then stale-while-reloading |
| Store search per postal code | `TTLCache` | a day |
| This week's Bonus (~300 requests) | `WeekCache` | until the bonus week ends |

`Scheduler` owns everything that runs outside a request: the Bonus pre-warm
job, hourly pruning of expired sign-ins, and one-off cache refreshes. It has
`start()`/`stop()`, so shutdown and tests are clean. The app runs as **one
process** (AH rotates the refresh token on every renewal), so there is no
cross-process coordination.

### Persistence

One SQLite file (`db.Database`: one connection behind a lock, WAL, foreign
keys). New tables go into `_SCHEMA` (`CREATE … IF NOT EXISTS`); changes to
existing tables are numbered `MIGRATIONS` steps, run in order from the
database's `user_version`. A database from a newer app version is refused.

## Browser code

Plain ES modules, no build step. `// @ts-check` with JSDoc types gives type
checking (`tsc`, see CLAUDE.md) without compiling anything.

```
static/js/
├── main.js        composition root: starts the modules, wires language,
│                  Refresh button and view switching
├── core/          no app imports: dom (html escaping), storage (all
│                  localStorage access), i18n, api, events, config, format,
│                  unit (unit prices), offers (Offer type, product identity)
├── lists/         liked and hidden products (+ their panel)        → core
├── ui/            shared card parts and widgets: cards, density,
│                  theme, view, dragdrop, reorder, rename           → core, lists
├── sync/          settings sync (merge.js is pure)                 → core
├── bargains/      the Bargains view                                → core, lists, ui
│   ├── index.js   controller: loads, owns the bargains, re-renders
│   ├── model.js   pure: filter, merge across stores, group, sort
│   └── filters, presets, stores, metrics, render
└── bonus/         the Bonus view                                   → core, lists, ui
    ├── index.js   controller + rendering
    ├── model.js   pure: savings, filters, categories, groups, deal index
    └── groups.js  the visitor's category groups
```

Rules (checked by `tests/test_architecture.py`):

- A folder imports only from the folders listed after it above. **The two
  views never import each other.**
- Modules talk through `core/events.js` (`EV.lists`, `EV.lang`, `EV.cols`,
  `EV.stores`, `EV.refilter`, `EV.settings`, `EV.needBonus`,
  `EV.bonusStatus`) instead of calling each other's render functions.
- State is owned by one module each (likes and hidden products by `lists/`,
  the store list by `bargains/stores.js`, the loaded bargains by
  `bargains/index.js`, the Bonus by `bonus/index.js`). Others read it through
  functions or receive it in an event; there is no shared mutable state object.
- Each view has one controller that decides what to show; filters, presets,
  stores and the overview only emit events.
- Logic that doesn't need the DOM lives in `model.js` / `merge.js` /
  `core/` and has unit tests (`tests/js/`, Node's test runner).
- All localStorage access goes through `core/storage.js`.

### Data flow example: liking a product

```
click 👍 (ui/cards.js) → lists/likes.toggleLike → save → emit(EV.lists)
    ├─► lists/likes      re-renders the liked list
    ├─► lists/panel      updates "3 liked · 2 hidden"
    ├─► bargains/index   refresh(): liked products break sort ties
    └─► bonus/index      renderBonus(): "Liked" pill, notice bar
```

## Extending

- **New AH data**: a query in `ah/queries.py` (or a REST path), a method on
  `AHGateway`, a mapping function in `ah/mapping.py` returning a domain model,
  a service in `services/` that caches it, a route in `api/routes.py` with
  `@rate_limited` and `@ah_required`. Tests fake the AH API (`query()`/`get()`).
  To offer it to AI assistants too: a `Tool` in `mcp/tools.py` (JSON Schema,
  argument checks, the same rate-limit `bucket`) calling the same service
  (shared logic lives on `container.Services`, e.g. `find_stores()`,
  `bargains()`, so `/api` and `/mcp` can't drift apart).
- **New setting**: a default in `config.BaseConfig` (env var of the same
  name), a field in the matching `Settings` group, `.env.example`.
- **New table or column**: see "Persistence" above.
- **New background job**: `svc.scheduler.every(name, interval, fn)` in
  `_register_jobs()`.
- **New UI area**: a template partial, a module in the right folder with an
  `init…()` called from its controller or `main.js`, events for anything other
  modules must react to, its text in both languages in `core/i18n.js`, and a
  `model.js` function + test for any logic that doesn't need the DOM.
