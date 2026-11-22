# AH Dashboard - production image.
#
#   docker build -t ah-dashboard .
#   docker run -d --name ah-dashboard --env-file .env -e HOST=0.0.0.0 \
#     -p 127.0.0.1:8733:8733 -v ah-instance:/app/instance -v ah-logs:/app/logs ah-dashboard
#
# or simply: docker compose up -d --build   (see compose.yaml)
#
# Secrets (AH tokens, ADMIN_PASSWORD_HASH, TOKEN_SEAL_SECRET) are passed in at
# run time through the environment; .env is never copied into the image.
# The SQLite database (users, settings, encrypted AH login) and the generated
# seal secret live in /app/instance and must be on a volume so it survives container restarts.

# Base image pinned by digest (reproducible builds). To update: docker pull
# python:3.12-slim, then put the new digest from `docker image inspect` here.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them. Exact versions with
# hashes from requirements.lock (regenerate after changing requirements.txt:
# see README "Dependencies").
COPY requirements.lock .
RUN pip install --require-hashes --no-deps -r requirements.lock

# Unprivileged user; only instance/ and logs/ are writable.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY ah_dashboard/ ah_dashboard/
COPY serve.py ./

# COPY keeps the host's file modes; if the files are private on the host
# (e.g. mode 600), the app user could not read them. Code: readable, not
# writable. instance/ and logs/: owned by the app user.
RUN chmod -R u=rwX,go=rX /app/ah_dashboard /app/serve.py \
 && mkdir -p /app/instance /app/logs && chown app:app /app/instance /app/logs

USER app

# Listen on all interfaces inside the container; publish the port on the host
# as narrowly as needed (compose binds it to 127.0.0.1 by default).
ENV APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8733 \
    THREADS=8

EXPOSE 8733

# /healthz is cheap: no database, never calls AH.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8733'), timeout=4)" || exit 1

# One process: token renewal and the bargains cache live in this process.
CMD ["python", "serve.py"]
