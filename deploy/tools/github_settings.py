"""Print the GitHub environments, variables and secrets the workflows need
(`deploy/ah-iac github`). Prints `gh` commands; it never sends anything itself."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import yaml

INVENTORY = Path(os.environ.get("AH_INVENTORY_DIR", "/work/deploy/ansible/inventory/production"))
IAC_HOME = os.environ.get("AH_HOST_IAC_HOME", "~/.config/ah-dashboard")
SSH_KEY = os.environ.get("AH_HOST_SSH_KEY", "~/.ssh/id_ed25519")


def main() -> None:
    hosts = yaml.safe_load((INVENTORY / "hosts.yml").read_text(encoding="utf-8"))
    host = hosts["all"]["hosts"]["vps"]
    values = yaml.safe_load((INVENTORY / "group_vars" / "all" / "vars.yml").read_text(encoding="utf-8"))
    q = shlex.quote
    print(f"""In GitHub: Settings -> Environments. Create two environments:

  production      used by the Release workflow to deploy (consider "Required reviewers"
                  and "Deployment branches: main").
  infrastructure  used by the Infrastructure workflow (full server setup); set
                  "Required reviewers": its key can change the whole server.

With the GitHub CLI (gh auth login first), from the repository folder:

  # production: the restricted deploy key (can only run ah-deploy)
  gh variable set DEPLOY_HOST --env production --body {q(str(host["ansible_host"]))}
  gh variable set DEPLOY_PORT --env production --body {q(str(host.get("ansible_port", 22)))}
  gh variable set DOMAIN      --env production --body {q(values["domain"])}
  gh variable set IMAGE_REPO  --env production --body {q(values["image_repo"])}
  gh secret set DEPLOY_SSH_KEY --env production < {IAC_HOME}/deploy_key

  # infrastructure: full access (Ansible user {host.get("ansible_user", "root")})
  gh secret set ANSIBLE_SSH_KEY        --env infrastructure < {SSH_KEY}
  gh secret set ANSIBLE_VAULT_PASSWORD --env infrastructure < {IAC_HOME}/vault-pass

The image repository must be ghcr.io/<owner of this GitHub repository>/ah-dashboard
(the Release workflow pushes there; the server only accepts that repository).
Commit deploy/ansible/inventory/ (vault.yml is encrypted) so the workflows can read it.""")


if __name__ == "__main__":
    main()
