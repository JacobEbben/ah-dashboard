# Plan: move sign-in to a central login subdomain

Status: proposal, nothing built yet. Domains below are placeholders:
`ah.example.nl` (this dashboard) and `login.example.nl` (the central login).

## 1. Goal and scope

Today the dashboard handles sign-in itself: it stores password hashes
(`users` table), checks passwords, locks out guessers, and the admin panel
creates and manages users. The goal is to hand **who you are** to one central
login service on its own subdomain, shared by this and future apps, so that:

- people have one account and one login page for all apps (single sign-on);
- passwords, password resets, two-factor and passkeys live in one hardened place;
- this app stops storing passwords at all.

What stays in this app: **what you may do here** (normal user or admin), the
synced settings, the server-side session list, and the AH login (the AH tokens
have nothing to do with user sign-in and are not affected).

Not in scope: changing the AH login, or making sign-in required (the dashboard
stays open to everyone; signing in still only syncs settings).

## 2. Recommended approach: OpenID Connect

The central login runs an **OpenID Connect (OIDC) identity provider**. The
dashboard becomes an OIDC client ("relying party") using the **authorization
code flow with PKCE**, done server-side:

1. "Sign in" sends the browser to `login.example.nl` with a random `state`,
   `nonce` and PKCE challenge (kept in this app's session cookie).
2. The person signs in there (password, passkey, 2FA: the login service's job).
3. The login service redirects back to `https://ah.example.nl/auth/callback?code=…&state=…`.
4. The dashboard server checks `state`, swaps the code for tokens directly with
   the login service (server to server), validates the ID token and reads the
   person's stable id (`sub`), name and groups.
5. The dashboard starts its **own** session exactly as now (`sessions` table,
   `__Host-` cookie on `ah.example.nl` only). The login service's tokens are
   not kept in the browser and not used as the dashboard session.

### Why not the alternatives

| Option | Verdict |
| --- | --- |
| **Caddy `forward_auth`** (Authelia/Authentik in front of the app, app trusts a `Remote-User` header) | Gates whole paths: fits "this page needs a login", not "open to everyone, signing in is optional". A header-trust mistake means anyone can be anyone. Still useful as an *extra* lock on `/admin` (see section 6). |
| **Shared cookie on `.example.nl`** (a home-made login that sets a cookie every subdomain reads) | Every subdomain (and every bug in any app on it) can read or plant the session; gives up the `__Host-` protection added for L1. Not recommended. |
| **SAML** | Heavier, no benefit here. |

### Which identity provider

All of these are self-hostable next to Caddy and speak OIDC:

| Provider | Fits when |
| --- | --- |
| **Pocket ID** | Small and simple, passkeys only (no passwords at all). Good for a family/friends setup. |
| **Authelia** | Light, file or LDAP users, 2FA, also does `forward_auth` for other apps. |
| **Authentik** / **Zitadel** | Full user management UI, self-service sign-up, many apps. Heavier (Postgres, Redis). |
| **Keycloak** | Most features, heaviest; overkill here. |

Recommendation: **Authelia** if other apps also need `forward_auth`
protection, otherwise **Pocket ID** for the simplest, password-free setup.
Decide before phase 1 (section 9).

## 3. What changes in this app

### New

- **OIDC client** (new module, e.g. `ah_dashboard/oidc.py`), using a maintained
  library (Authlib) rather than hand-written token checks. Routes:
  - `GET /auth/login`: create `state`, `nonce`, PKCE verifier, redirect to the provider.
  - `GET /auth/callback`: verify `state`, exchange the code, validate the ID token, start the session.
  - `POST /auth/logout`: end the local session, then (optionally) the provider's session (RP-initiated logout).
  - `POST /auth/backchannel-logout` (optional, if the provider supports it): the provider tells the app a person signed out or was disabled; delete their rows in `sessions`.
- **Settings**: `OIDC_ISSUER` (https only, validated like the AH URLs),
  `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` (in `.env` only, never in
  `.env.example`), `OIDC_ADMIN_GROUP` (e.g. `ah-dashboard-admins`),
  `AUTH_MODE` = `local` | `both` | `oidc` (for the transition, section 7).
- **Identity table**: users are keyed by the provider's `sub`, not by a
  username someone typed. Add `users.oidc_sub` (unique) or a new
  `identities (sub PRIMARY KEY, username REFERENCES users)` table. Changing the
  existing `users` table needs an upgrade step in `Database.__init__` and a
  `SCHEMA_VERSION` bump to 3 (CLAUDE.md); a separate new table does not.
  Recommended: the new table, so a rollback to the old version still opens the database.

### Changed

- **`auth/access.py`**: `login_user()` is called from the OIDC callback instead of
  the password form. `current_user()` keeps checking `sessions`, `disabled` and
  `session_version`, so disabling a user locally still works instantly.
- **Admin rights** (`auth/admin.py`): taken from the ID token's group
  claim at sign-in (`OIDC_ADMIN_GROUP`), stored on the session, and re-checked
  when the session is renewed. `users.is_admin` becomes a read-only mirror, or
  is dropped after the switch. The "last admin" rule (L5) disappears, because
  admins are managed in the provider.
- **Session lifetime**: shorter dashboard sessions (e.g. 7 days instead of 30),
  renewed silently with `prompt=none` while the provider session lasts, so that
  a person disabled in the provider loses access within days even without
  back-channel logout.
- **Templates**: `templates/login.html` and `templates/admin/login.html`
  become a "Sign in with …" button (a plain link, no password fields); the
  sign-in link in `partials/_controls.html` points to `/auth/login`.
- **Admin panel** (`admin/routes.py`, `templates/admin/index.html`): the
  "Dashboard users" section loses add / set password; keeps disable / enable /
  delete (local data) and shows each user's provider identity and last sign-in.
  The AH token and health sections are unchanged.
- **CSP** (`headers.py`): `form-action 'self'` blocks a form whose response
  redirects to another origin in Chrome. The logout form redirects to the
  provider, so add `form-action 'self' https://login.example.nl` (derived from
  `OIDC_ISSUER`, like `CSP_IMAGE_HOSTS`). `connect-src` stays `'self'`: the
  token exchange is server-side.
- **Outbound requests**: the server now also talks to the provider (discovery
  document, token endpoint, signing keys). Use the same rules as the AH client:
  https only, no redirects followed, no environment proxies, fixed URLs from
  the discovery document of `OIDC_ISSUER` only, timeouts, response size limit.

### Removed (in the final phase)

- Password hashing and checks (`UserStore.create/verify/set_password`,
  `password_check_slot`, `MIN_PASSWORD`, `USER_PASSWORD_METHOD`).
- Login lockouts for users (`user_limiter`, `ACCOUNT_MAX_FAILURES`,
  `LOGIN_ATTEMPTS_PER_MINUTE` for `/login`): guessing protection moves to the
  provider. This also closes L2 and L3 from the security review.
- The `password_hash` column (only after the migration is done, with a schema step).

## 4. Security requirements

Must-haves, each with a test:

- `state` (CSRF on the callback), `nonce` (replay), PKCE (`S256`): random,
  single-use, kept in the session, expire after ~10 minutes.
- Exact `redirect_uri` registered at the provider; no wildcards.
- ID token checks: signature against the provider's published keys (only
  `RS256`/`ES256`/`EdDSA`, never `none` or `HS*` with a public key), `iss`
  equals `OIDC_ISSUER`, `aud` contains our client id, `exp`/`iat` with small
  clock skew, `nonce` matches.
- After sign-in, only redirect to a relative path inside the app (no open
  redirect via a `next` parameter).
- Session fixation: keep using `end_session()` + new session id on sign-in (as now).
- Tokens from the provider never in logs, URLs shown to users, or responses;
  only `sub`, name and groups are kept.
- Session cookie stays host-only `__Host-ahb_admin` on `ah.example.nl`; the
  login service uses its own cookies on its own host.
- `SameSite=Lax` stays: the callback is a top-level GET, so the session cookie
  (with `state`) is sent on it.
- Rate-limit `/auth/callback` per client key like the other endpoints.
- Errors from the provider: generic message plus reference, details in the log
  (existing pattern).

## 5. Data migration (existing users)

Existing users have a username, password hash and synced settings
(`user_prefs`, keyed by username). They need to be linked to a provider
account without losing settings:

1. Create the same people in the provider (same e-mail or username).
2. **Link on first central sign-in**, one of:
   - **Automatic by verified e-mail**: if the provider sends a verified
     `email` that matches a local user, link them. Requires adding e-mail to
     local users first; only safe with verified e-mail claims.
   - **Explicit link** (safest): during the `both` phase, a person signed in
     the old way clicks "Link my central login" once; the callback stores their
     `sub` against their existing username.
   - **Admin link**: the admin panel lets an admin attach a `sub` to a user.
3. A `sub` that matches nobody: create a new local user record (no password)
   with empty settings.
4. When everyone is linked (the admin panel shows who isn't), switch to `oidc` only.

Settings sync (`/api/prefs`, `sync.js`) needs no change: it already works per
signed-in user.

## 6. Admin access and the emergency door

- Admins are people in `OIDC_ADMIN_GROUP` at the provider.
- Optional extra lock: Caddy `forward_auth` (Authelia) or an IP allowlist in
  front of `/admin`, so the panel isn't even reachable without the central login.
- **Break-glass**: if the login service is down, nobody can reach `/admin`. Keep
  the local `ADMIN_PASSWORD_HASH` sign-in, but only reachable from the server
  itself (an SSH tunnel to `127.0.0.1:8733`), not through Caddy. Document how to use it.

## 7. Phases and rollout

| Phase | Work | Rollback |
| --- | --- | --- |
| 0. Decide | Pick the provider; answer the open questions (section 9). | n/a |
| 1. Login service | Deploy the provider on `login.example.nl` behind Caddy with its own volume, backups, SMTP for e-mails, 2FA/passkeys. Register this app as a client. | Remove it; the app is untouched. |
| 2. OIDC in the app, off | Build section 3 behind `AUTH_MODE=local` (default). Tests with a fake provider. | Code is inactive. |
| 3. Both | `AUTH_MODE=both`: password sign-in and "Sign in with …" side by side; account linking (section 5); admin rights from the group claim. | Back to `local`. |
| 4. Central only | `AUTH_MODE=oidc`: password forms gone, break-glass admin only via the server. Shorter sessions, back-channel logout. | Back to `both` (password hashes still stored). |
| 5. Clean up | After a few weeks without problems: drop password code, lockouts for users and the `password_hash` column (schema step + version bump). | Restore a database backup from before the step. |

Every phase keeps the existing rules: CSP tests, no tokens in logs, English
and Dutch text for every new message, Knight design for the new buttons.

## 8. Testing

- A **fake OIDC provider** in the tests: generate a signing key per test run,
  serve a discovery document and keys from a local stub (or mock the HTTP
  layer), and sign ID tokens with chosen claims. Tests never contact the real provider.
- Cases: happy path; wrong/missing/reused `state`; wrong `nonce`; expired
  token; wrong `iss`/`aud`; `alg: none`; unknown signing key; token signed with
  the wrong key; open-redirect attempts; group claim present/absent/removed;
  back-channel logout ends the right sessions; linking cannot attach a `sub`
  to someone else's account; CSP `form-action` contains exactly the issuer origin.
- Browser check (isolated instance, as in CLAUDE.md) of the sign-in button,
  callback and logout in light, dark and at 375px.

## 9. Open questions

1. Which provider (section 2)? Will other apps use it, and do they need `forward_auth`?
2. Who may create accounts: only the admin (invite), or self-service sign-up?
3. Passwords at the provider, or passkeys only?
4. Link existing users by verified e-mail, by an explicit link step, or by the admin?
5. Is the break-glass admin password (server-local only) acceptable, or should
   `/admin` depend fully on the login service?
6. Session length for the dashboard after the move (proposal: 7 days, renewed silently).

## 10. Rough effort

| Part | Estimate |
| --- | --- |
| Login service deployment (Caddy, volume, backups, SMTP, first users) | 0.5–1 day |
| OIDC client, routes, settings, CSP, outbound rules | 1–2 days |
| Account linking, admin via groups, admin panel changes | 1 day |
| Fake provider and tests, browser checks, docs | 1–1.5 days |
| Cleanup phase | 0.5 day |
