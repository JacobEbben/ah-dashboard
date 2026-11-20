"""Run the app with Waitress (the container's entry point: ``python serve.py``).

Settings come from the environment (compose passes .env in; see .env.example),
e.g. AH_ACCESS_TOKEN, AH_REFRESH_TOKEN, HOST, PORT, THREADS.

Behind a reverse proxy (Caddy), set TRUSTED_PROXY to the proxy's address (or
"*" when only the proxy can reach the app, e.g. no published port). Waitress
then takes the client address from X-Forwarded-For, so login lockouts and
rate limits apply per visitor instead of to everyone at once.
"""

import os

from waitress import serve

from ah_dashboard import create_app
from ah_dashboard.config import unquote


def waitress_options(env=os.environ) -> dict:
    def get(key: str, default: str) -> str:
        return unquote(env.get(key, default).strip())

    options = {
        "host": get("HOST", "127.0.0.1"),
        "port": int(get("PORT", "8733")),
        "threads": int(get("THREADS", "8")),
        "ident": "",  # no "Server: waitress" header
    }
    proxy = get("TRUSTED_PROXY", "")
    if proxy:
        options.update(
            trusted_proxy=proxy,
            trusted_proxy_count=int(get("TRUSTED_PROXY_COUNT", "1")),
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
            clear_untrusted_proxy_headers=True,
        )
    return options


def main() -> None:
    options = waitress_options()
    app = create_app()
    print(f"AH Dashboard running at http://{options['host']}:{options['port']}"
          + (f" (behind proxy {options['trusted_proxy']})" if "trusted_proxy" in options else ""))
    serve(app, **options)


if __name__ == "__main__":
    main()
