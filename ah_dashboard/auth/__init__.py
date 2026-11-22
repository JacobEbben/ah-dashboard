"""Sign-in and access control, in one place.

The dashboard is open to everyone; signing in only syncs a user's settings.
The admin panel needs the admin password or a user promoted to admin.

Modules (no module here imports a blueprint):

- :mod:`.users` — accounts (linked to login-service users; no passwords) and
  their synced settings.
- :mod:`.sessions` — the server-side list of active sign-ins plus starting
  and ending the signed session cookie (``begin`` / ``end``).
- :mod:`.access` — dashboard users: ``login_user``, ``current_user``,
  ``require_user_api``.
- :mod:`.admin` — the admin session (admin password or admin user),
  ``require_admin`` / ``require_admin_api``.
- :mod:`.csrf` — the per-session CSRF token (forms and ``X-CSRF-Token``).
- :mod:`.compare` — ``same_secret``: constant-time comparison of tokens (never
  ``hmac.compare_digest`` on ``str`` directly: non-ASCII input raises).
- :mod:`.lockout` — failed sign-in counting (per client and per account),
  the server-wide sign-in cap and the password-check slots.

One signed session cookie (``ahb_admin`` / ``__Host-ahb_admin``) carries both
the user and the admin part; see ``create_app`` for its settings.
"""
