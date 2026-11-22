# AH Dashboard

Albert Heijn offers on one page: the **Bargains** ("Laatste Kans Koopjes") of chosen stores, and this week's
(and next week's) **Bonus**. The server uses one operator-configured AH login; visitors need no account.
Optional sign-in (OpenID Connect) syncs settings. Includes `/admin`, `/privacy`, `/voorwaarden` and a read-only `/mcp`.

## Run

```bash
cp .env.example .env
docker compose run --rm ah-dashboard python -m ah_dashboard.admin   # ADMIN_PASSWORD_HASH (keep it single-quoted)
docker compose up -d --build                                        # http://127.0.0.1:8733
```

In `/admin`: set the AH login and, optionally, the login service. Settings are documented in `.env.example`.

**Production**: run exactly **one** instance (AH rotates its token) behind HTTPS with `APP_ENV=production`,
`ADMIN_COOKIE_SECURE=true`, `PUBLIC_BASE_URL`, and `TRUSTED_PROXY=*` when only the proxy can reach it.
Keep the `/app/instance` volume. `.github/workflows/release.yml` builds, tests and deploys on push to `main`.

## Checks

```bash
docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh ci/check.sh python
docker run --rm -v "$PWD:/src:ro" -w /src node:22-slim sh ci/check.sh js
docker run --rm -v "$PWD:/src:ro" -w /src python:3.12-slim sh ci/check.sh lock
```

Details: `CLAUDE.md`. Licence: MIT (`LICENSE`); fonts: SIL OFL (`ah_dashboard/static/fonts/OFL.txt`).
Not affiliated with Albert Heijn.
