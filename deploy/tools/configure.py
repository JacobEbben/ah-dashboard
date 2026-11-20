"""Interactive setup of the deployment values (run through `deploy/ah-iac configure`).

Writes, under deploy/ansible/inventory/<name>/:
  hosts.yml                  the server (address, SSH user and port)
  group_vars/all/vars.yml    domain, certificates, image, SSH keys, app settings
  group_vars/all/vault.yml   secrets, encrypted with ansible-vault
  known_hosts                the server's SSH host keys (after you confirm them)

Run it again to change values: every question shows the current value, Enter
keeps it. Secrets are never printed; the token seal secret is created once and
never replaced (that would make the stored AH login unreadable).
"""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
import socket
import subprocess
import sys
from getpass import getpass
from pathlib import Path

import yaml
from werkzeug.security import generate_password_hash

INVENTORY = Path(os.environ.get("AH_INVENTORY_DIR", "/work/deploy/ansible/inventory/production"))
VAULT_PASS = os.environ.get("AH_VAULT_PASS_FILE", "/secrets/vault-pass")
ADMIN_PUB = Path(os.environ.get("AH_ADMIN_PUB", "/secrets/admin_key.pub"))
DEPLOY_PUB = Path(os.environ.get("AH_DEPLOY_PUB", "/secrets/deploy_key.pub"))

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*$")
KEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+|sk-\S+) [A-Za-z0-9+/=]+( .*)?$")
TLS_MODES = ("letsencrypt", "letsencrypt-staging", "internal")
MIN_PASSWORD = 12   # like python -m ah_dashboard.admin


# ansible-vault gets the password file explicitly; the same file also set in
# the environment (ah-iac does) would count as a second "default" vault id.
VAULT_ENV = {k: v for k, v in os.environ.items() if k != "ANSIBLE_VAULT_PASSWORD_FILE"}


# ---- reading / writing --------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_vault(path: Path) -> dict:
    if not path.exists():
        return {}
    out = subprocess.run(["ansible-vault", "view", "--vault-password-file", VAULT_PASS, str(path)],
                         capture_output=True, text=True, check=False, env=VAULT_ENV)
    if out.returncode != 0:
        sys.exit("Can't decrypt vault.yml with this vault password file (wrong file?): " + out.stderr.strip())
    data = yaml.safe_load(out.stdout)
    return data if isinstance(data, dict) else {}


def write_vault(path: Path, values: dict) -> None:
    text = "# Secrets for the AH Dashboard deployment (ansible-vault encrypted).\n" + yaml.safe_dump(values, sort_keys=True)
    out = subprocess.run(["ansible-vault", "encrypt", "--vault-password-file", VAULT_PASS, "--output", str(path), "-"],
                         input=text, capture_output=True, text=True, check=False, env=VAULT_ENV)
    if out.returncode != 0:
        sys.exit("ansible-vault encrypt failed: " + out.stderr.strip())


def dump(header: str, values: dict) -> str:
    return header + yaml.safe_dump(values, sort_keys=False, default_flow_style=False, width=1000)


# ---- questions --------------------------------------------------------------------------------
def ask(question: str, default: str = "", check=None, hint: str = "") -> str:
    while True:
        shown = f" [{default}]" if default else ""
        answer = input(f"{question}{shown}: ").strip() or default
        if check is None or check(answer):
            return answer
        print(f"  ! {hint or 'Not valid, try again.'}")


def ask_yes(question: str, default: bool) -> bool:
    answer = ask(question + " (y/n)", "y" if default else "n", lambda a: a.lower() in ("y", "n", "yes", "no"))
    return answer.lower().startswith("y")


def ask_secret(question: str, have: bool) -> str | None:
    """None = keep the current value (or leave it empty)."""
    note = " (Enter keeps the current one)" if have else " (Enter leaves it empty)"
    value = getpass(f"{question}{note}: ").strip()
    return value or None


def ask_admin_password(have: bool) -> str | None:
    while True:
        pw = getpass("Admin password for /admin" + (" (Enter keeps the current one)" if have else "") + ": ")
        if not pw and have:
            return None
        if len(pw) < MIN_PASSWORD:
            print(f"  ! Use at least {MIN_PASSWORD} characters.")
            continue
        if getpass("Repeat it: ") != pw:
            print("  ! The passwords don't match.")
            continue
        return generate_password_hash(pw)   # the same hash as python -m ah_dashboard.admin


def read_key(path: Path) -> str:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return key if KEY_RE.match(key) else ""


# ---- checks -----------------------------------------------------------------------------------
def addresses(name: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(name, None)}
    except OSError:
        return set()


def check_dns(domain: str, host: str) -> None:
    try:
        server = {str(ipaddress.ip_address(host))}
    except ValueError:
        server = addresses(host)
    found = addresses(domain)
    if not found:
        print(f"  ! {domain} does not resolve yet. Create an A (and AAAA) record pointing to the server before "
              "provisioning: Let's Encrypt checks it.")
    elif server and not (found & server):
        print(f"  ! {domain} points to {', '.join(sorted(found))}, not to the server ({', '.join(sorted(server))}).")
    else:
        print(f"  DNS ok: {domain} -> {', '.join(sorted(found))}")


def confirm_host_keys(host: str, port: int, known_hosts: Path) -> None:
    scan = subprocess.run(["ssh-keyscan", "-T", "10", "-p", str(port), host], capture_output=True, text=True, check=False)
    lines = [line for line in scan.stdout.splitlines() if line and not line.startswith("#")]
    if not lines:
        print(f"  ! Could not read SSH host keys from {host}:{port}. Run configure again when the server is up.")
        return
    prints = subprocess.run(["ssh-keygen", "-lf", "-"], input="\n".join(lines) + "\n", capture_output=True,
                            text=True, check=False)
    print("\nThe server presents these SSH host keys. Compare them with the server console or your provider's\n"
          "panel (on the server: for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf $f; done):")
    for line in prints.stdout.splitlines():
        print("   ", line)
    if ask_yes("Do they match", False):
        known_hosts.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  Saved to {known_hosts.relative_to(INVENTORY.parent.parent.parent)}")
    else:
        print("  ! Not saved: Ansible and CI will refuse to connect until the host keys are confirmed.")


# ---- main --------------------------------------------------------------------------------
def main() -> int:
    hosts_file = INVENTORY / "hosts.yml"
    vars_file = INVENTORY / "group_vars" / "all" / "vars.yml"
    vault_file = INVENTORY / "group_vars" / "all" / "vault.yml"
    known_hosts = INVENTORY / "known_hosts"
    vars_file.parent.mkdir(parents=True, exist_ok=True)

    host_entry = (load_yaml(hosts_file).get("all", {}).get("hosts", {}) or {}).get("vps", {}) or {}
    cur = load_yaml(vars_file)
    vault = load_vault(vault_file)
    settings = dict(cur.get("app_settings") or {})

    print("== Server ==")
    host = ask("Server address (IP or host name)", str(host_entry.get("ansible_host", "")),
               lambda a: bool(re.fullmatch(r"[A-Za-z0-9.:-]+", a)), "An IP address or host name.")
    user = ask("SSH user Ansible logs in as (root or a sudo user)", str(host_entry.get("ansible_user", "root")),
               lambda a: bool(re.fullmatch(r"[a-z_][a-z0-9_-]*", a)))
    port = int(ask("SSH port", str(host_entry.get("ansible_port", 22)), lambda a: a.isdigit() and 0 < int(a) < 65536))

    print("\n== Site and certificate ==")
    domain = ask("Domain of the dashboard (e.g. ah.example.nl)", cur.get("domain", ""),
                 lambda a: bool(DOMAIN_RE.match(a)), "Lower case, e.g. ah.example.nl.")
    check_dns(domain, host)
    print("Certificates: letsencrypt (real), letsencrypt-staging (test CA, no rate limits: good for a first try),\n"
          "internal (Caddy's own CA, browsers warn: for servers without public DNS).")
    tls_mode = ask("Certificate mode", cur.get("tls_mode", "letsencrypt"), lambda a: a in TLS_MODES,
                   "One of: " + ", ".join(TLS_MODES))
    acme_email = cur.get("acme_email", "")
    if tls_mode != "internal":
        acme_email = ask("E-mail for Let's Encrypt (expiry notices)", acme_email, lambda a: bool(EMAIL_RE.match(a)))
    redirect_www = ask_yes(f"Also serve www.{domain} (redirects to {domain}; needs its own DNS record)",
                           bool(cur.get("redirect_www", False)))

    print("\n== Image and access ==")
    owner = os.environ.get("AH_GITHUB_OWNER", "").lower()
    image_repo = ask("Image repository (GitHub Container Registry)",
                     cur.get("image_repo", f"ghcr.io/{owner}/ah-dashboard" if owner else ""),
                     lambda a: bool(REPO_RE.match(a)), "Lower case, e.g. ghcr.io/<owner>/ah-dashboard.")
    admin_keys = list(cur.get("admin_ssh_keys") or [])
    mine = read_key(ADMIN_PUB)
    if mine and mine not in admin_keys and ask_yes(f"Add your public key ({mine.split()[-1]}) as admin key", True):
        admin_keys.append(mine)
    if not admin_keys:
        print("  ! No admin SSH key: SSH hardening will be skipped so you can't lock yourself out.")
    deploy_key = read_key(DEPLOY_PUB) or cur.get("deploy_ssh_key", "")

    print("\n== App settings (Enter keeps the value; '-' clears it) ==")
    for key, question in (("PRIVACY_CONTACT", "Privacy contact shown on /privacy (e-mail or URL)"),
                          ("GOOGLE_CLIENT_ID", "Google OAuth client id (empty: no Google sign-in)"),
                          ("MCP_ENABLED", "MCP endpoint for AI assistants (true/false)"),
                          ("MAX_USERS", "Max. dashboard users")):
        defaults = {"MCP_ENABLED": "true", "MAX_USERS": "1000"}
        value = ask(question, str(settings.get(key, defaults.get(key, ""))))
        if value in ("", "-"):
            settings.pop(key, None)
        else:
            settings[key] = value

    print("\n== Secrets (stored encrypted in vault.yml) ==")
    if not vault.get("vault_token_seal_secret"):
        vault["vault_token_seal_secret"] = secrets.token_urlsafe(48)
        print("  Created a new token seal secret (encrypts the AH login in the database; never change it).")
    else:
        print("  Token seal secret: kept (it must never change).")
    admin_hash = ask_admin_password(bool(vault.get("vault_admin_password_hash")))
    if admin_hash:
        vault["vault_admin_password_hash"] = admin_hash
    if settings.get("GOOGLE_CLIENT_ID") and (
            value := ask_secret("Google OAuth client secret", bool(vault.get("vault_google_client_secret")))):
        vault["vault_google_client_secret"] = value
    print("  The AH login is easiest set later in /admin ('Log in with an AH login code').")
    if value := ask_secret("AH refresh token (optional)", bool(vault.get("vault_ah_refresh_token"))):
        vault["vault_ah_refresh_token"] = value
    if ask_yes("Is the image package private (the server then needs a registry login)",
               bool(vault.get("vault_registry_token"))):
        vault["vault_registry_user"] = ask("Registry user (GitHub user name)", vault.get("vault_registry_user", ""))
        if value := ask_secret("Registry token (GitHub PAT with read:packages only)",
                               bool(vault.get("vault_registry_token"))):
            vault["vault_registry_token"] = value
    else:
        vault.pop("vault_registry_user", None)
        vault.pop("vault_registry_token", None)
    bad = [k for k, v in vault.items() if re.search(r"[\r\n']", str(v))]
    if bad:
        sys.exit("These values contain a line break or a single quote, which app.env can't hold: " + ", ".join(bad))

    hosts_file.write_text(dump(
        "# The server (written by `deploy/ah-iac configure`; safe to edit).\n",
        {"all": {"hosts": {"vps": {"ansible_host": host, "ansible_user": user, "ansible_port": port}}}}),
        encoding="utf-8")
    vars_file.write_text(dump(
        "# Deployment settings (written by `deploy/ah-iac configure`; safe to edit, then `ah-iac provision`).\n"
        "# Role defaults and what each value does: deploy/ansible/roles/*/defaults/main.yml.\n"
        "# Secrets are in vault.yml (`ah-iac vault-edit`).\n",
        {"domain": domain, "tls_mode": tls_mode, "acme_email": acme_email, "redirect_www": redirect_www,
         "image_repo": image_repo, "admin_ssh_keys": admin_keys, "ssh_harden": bool(admin_keys),
         "deploy_ssh_key": deploy_key, "app_settings": settings}),
        encoding="utf-8")
    write_vault(vault_file, vault)
    print(f"\nSaved {hosts_file.name}, group_vars/all/vars.yml and the encrypted group_vars/all/vault.yml.")

    if ask_yes(f"Read and confirm the SSH host keys of {host} now", True):
        confirm_host_keys(host, port, known_hosts)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nStopped; nothing more was written.")
        sys.exit(130)
