"""AH Dashboard: Albert Heijn last-chance bargains per store ("Laatste Kans
Koopjes") and the weekly national Bonus.

:func:`create_app` is the composition root: it reads the configuration,
builds every service once (``container.Services``), registers the background
jobs and the blueprints. ``serve.py`` (the container's entry point) and the
tests go through it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from flask import Flask

from . import errors, headers, logging_setup, sealing
from .ah.auth import TokenManager, TokenStore
from .ah.client import AHClient
from .ah.session import AHSession
from .auth.google import GoogleSignIn
from .auth.lockout import LoginLimiter
from .auth.sessions import SessionStore
from .auth.users import UserStore
from .config import Settings, load_config
from .container import EXTENSION_KEY, Services
from .db import Database
from .ratelimit import RateLimiter
from .scheduler import Scheduler
from .services import bargains as bargains_service
from .services import bonus as bonus_service
from .services.bonus import BonusStore, NextWeekStatus, WeekCache
from .services.cache import TTLCache
from .services.stores import KnownStores

log = logging.getLogger(__name__)

# Labels of the derived keys. They keep the project's old name ON PURPOSE:
# changing them makes the stored AH login unreadable and signs everyone out.
SESSION_KEY_LABEL = b"ah-bargains flask session v1"
TOKEN_STORE_LABEL = b"ah-bargains token store v1"


def create_app(config: str | type | None = None, overrides: Mapping[str, Any] | None = None) -> Flask:
    """Build and configure the Flask application.

    ``config`` is a profile name ("development", "production", "testing"), a
    profile class, or ``None`` to read ``APP_ENV`` from the environment.
    ``overrides`` replaces individual settings (after the environment).
    """
    app = Flask(__name__)
    app.config.update(load_config(config, overrides))
    settings = Settings.from_config(app.config)

    logging_setup.configure(app)
    secret = sealing.server_secret(app)
    app.secret_key = sealing.derive_key(secret, SESSION_KEY_LABEL)
    _configure_cookie(app, settings)

    app.extensions[EXTENSION_KEY] = svc = _build_services(app, settings, secret)
    _register_jobs(svc)

    from .admin import bp as admin_bp
    from .api import bp as api_bp
    from .mcp import bp as mcp_bp
    from .web import bp as web_bp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(mcp_bp, url_prefix="/mcp")
    errors.register(app)
    headers.register(app, image_hosts=settings.app.csp_image_hosts, hsts=settings.auth.cookie_secure)

    if settings.app.background_jobs:
        svc.scheduler.start()
    return app


def _configure_cookie(app: Flask, settings: Settings) -> None:
    """One signed session cookie for the admin and the dashboard users.

    Lax (not Strict): a link to the dashboard from another site keeps the
    user signed in; every state-changing request carries a CSRF token. Via
    HTTPS the "__Host-" prefix makes browsers refuse the cookie unless it is
    Secure, for this exact host and path "/": a subdomain can't plant or
    overwrite it. (Switching ADMIN_COOKIE_SECURE renames the cookie, which
    signs everyone out once.) The cookie lives as long as a user session; an
    admin session ends sooner (ADMIN_SESSION_MINUTES without admin activity,
    ADMIN_SESSION_MAX_HOURS after its sign-in at the latest; auth/admin.py).
    """
    secure = settings.auth.cookie_secure
    app.config.update(
        SESSION_COOKIE_NAME="__Host-ahb_admin" if secure else "ahb_admin",
        SESSION_COOKIE_DOMAIN=None,
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure,
        PERMANENT_SESSION_LIFETIME=timedelta(days=settings.auth.user_session_days),
    )
    a = settings.auth
    if a.google_enabled and not a.public_base_url and secure and not app.config["TESTING"]:
        log.warning("Google sign-in is on but PUBLIC_BASE_URL is unset: the return address is taken "
                    "from the request. Set PUBLIC_BASE_URL to the site's https:// address.")
    if not secure and not app.config["TESTING"] and not app.config["DEBUG"]:
        log.warning("ADMIN_COOKIE_SECURE is off: the session cookie is also sent over plain HTTP. "
                    "Set ADMIN_COOKIE_SECURE=true when the app is served via HTTPS.")


def _build_services(app: Flask, settings: Settings, secret: str) -> Services:
    ah, c, a = settings.ah, settings.cache, settings.auth
    scheduler = Scheduler()
    db = Database(_database_path(app, settings))

    # The server's AH login (configured tokens + automatic renewal), and the
    # session that sends AH requests with it.
    tokens = TokenManager(
        store=TokenStore(db, sealing.derive_fernet(secret, TOKEN_STORE_LABEL)),
        env_access=ah.access_token, env_refresh=ah.refresh_token,
        refresh_url=ah.auth_refresh_url, client_id=ah.client_id,
        timeout=ah.timeout, cooldown=ah.refresh_cooldown,
    )
    session = AHSession(tokens, lambda token: AHClient(
        token=token, url=ah.graphql_url, timeout=ah.timeout, user_agent=ah.user_agent,
        rest_url=ah.rest_url, rest_headers=ah.rest_headers,
    ), max_concurrent=ah.max_concurrent, rate=ah.rate_per_second, burst=ah.rate_burst,
        throttle_pause=ah.throttle_pause)

    # Next week's Bonus, loaded on request once AH publishes it.
    next_cache = WeekCache(retry_seconds=c.bonus_retry, store=BonusStore(db, "bonus_next_week"), next_week=True)

    return Services(
        settings=settings,
        db=db,
        users=UserStore(db, max_users=a.max_users),
        sessions=SessionStore(db, max_age=max(a.user_session_days * 86400, a.admin_session_max_hours * 3600)),
        tokens=tokens,
        ah=session,
        scheduler=scheduler,
        rate_limiter=RateLimiter(),
        admin_limiter=LoginLimiter(),
        google=GoogleSignIn(a.google_client_id, a.google_client_secret, timeout=ah.timeout)
        if a.google_client_id and a.google_client_secret else None,
        known_stores=KnownStores(db),
        # Per-store bargains, shared by all visitors. Old entries are served
        # while they reload in the background; failed stores are not asked
        # again for a while.
        bargains_cache=TTLCache(
            ttl=c.bargains_ttl, max_entries=c.bargains_max_stores, stale_ttl=c.bargains_stale,
            error_ttl=c.store_error_ttl, cache_error=bargains_service.cache_error,
            spawn=lambda fn: scheduler.spawn(fn, "cache-refresh")),
        stores_cache=TTLCache(ttl=c.store_search_ttl, max_entries=2000),
        # The national Bonus: one shared copy, kept until the bonus week ends.
        bonus_cache=WeekCache(retry_seconds=c.bonus_retry, store=BonusStore(db)),
        bonus_next_cache=next_cache,
        bonus_next_status=NextWeekStatus(next_cache),
    )


def _register_jobs(svc: Services) -> None:
    """Periodic background jobs (started with the scheduler, see BACKGROUND_JOBS)."""
    if svc.settings.cache.bonus_prewarm:
        # Keep the Bonus loaded (start-up, week change), so visitors never wait
        # for its ~300 AH requests. Reads svc.ah at run time (tests swap it).
        svc.scheduler.every("bonus", 60, bonus_service.prewarm_job(
            svc.bonus_cache, svc.load_bonus_week, ready=lambda: svc.gateway.configured),
            backoff=svc.settings.cache.bonus_retry)

    def hourly_cleanup() -> None:
        svc.sessions.prune()      # expired sign-ins (also removed at every new sign-in)
        svc.db.checkpoint()       # empty the write-ahead log (db.WAL_SIZE_LIMIT)

    svc.scheduler.every("hourly-cleanup", 3600, hourly_cleanup)


def _database_path(app: Flask, settings: Settings) -> str | None:
    """DATABASE_FILE, else instance/ah_dashboard.db; "" = in memory (tests)."""
    configured = settings.app.database_file
    if configured == "":
        return None
    return configured or os.path.join(app.instance_path, "ah_dashboard.db")
