# Ansible toolbox for deploy/: everything `deploy/ah-iac` and the GitHub
# workflows need (ansible-core, ansible-lint, the collections, ssh, the admin
# password hasher). Nothing is installed on your own machine.
#
#   docker build -t ah-iac-tools -f deploy/tools/Dockerfile deploy
#
# Same digest-pinned base as the app image; Python packages from a hashed lock.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections \
    ANSIBLE_CONFIG=/work/deploy/ansible/ansible.cfg

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client sshpass git \
 && rm -rf /var/lib/apt/lists/*

COPY tools/requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --no-deps -r /tmp/requirements.lock

COPY ansible/requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install --no-cache -r /tmp/requirements.yml -p "$ANSIBLE_COLLECTIONS_PATH"

WORKDIR /work
