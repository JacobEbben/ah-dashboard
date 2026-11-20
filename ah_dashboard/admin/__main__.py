"""Create the admin password hash for .env:

    python -m ah_dashboard.admin

Prints a line ADMIN_PASSWORD_HASH=... to put in .env (only the hash is
stored; the password itself is never written anywhere).
"""

import getpass
import sys

from werkzeug.security import generate_password_hash

MIN_LENGTH = 12


def main() -> int:
    password = getpass.getpass("New admin password: ")
    if len(password) < MIN_LENGTH:
        print(f"Use at least {MIN_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    print("\nAdd this line to .env and restart the server:\n")
    # Single quotes: the hash contains "$", which Docker Compose would
    # otherwise treat as a variable. Compose removes the quotes, and so does
    # config.unquote when the environment comes from elsewhere.
    print("ADMIN_PASSWORD_HASH='" + generate_password_hash(password) + "'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
