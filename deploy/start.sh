#!/bin/bash
# Container entrypoint. Runs the s6 supervisor directly over the service directories.
#
# s6-overlay's own /init is not used: it insists on being process 1, and Runpod starts
# containers under its own init, so /init exits with "can only run as pid 1".
# s6-svscan has no such requirement. It starts every service in /opt/docqa/services and
# restarts any that exit; services inherit this process's environment.
set -euo pipefail
export PATH="/command:$PATH"

# Create the JWT signing key here, once, before any service starts: two services read it,
# and generating it in each would race on first boot.
secret="${DOCQA_DATA_DIR:-/workspace/docqa}/secrets/jwt_secret"
if [ -z "${DOCQA_JWT_SECRET:-}" ] && [ ! -s "$secret" ]; then
  mkdir -p "$(dirname "$secret")"
  head -c 48 /dev/urandom | base64 | tr -d '\n' > "$secret.partial"
  mv "$secret.partial" "$secret"
  chown docqa:docqa "$secret" 2>/dev/null || true
  chmod 600 "$secret" 2>/dev/null || true
fi

# A stop request takes one last backup before anything shuts down. Left to itself,
# s6-svscan signals every service at once, so PostgreSQL and Qdrant were gone before the
# backup could reach them. So this script handles the signal: it stops the backup loop
# (letting a backup in progress finish), backs up while the databases are still up, then
# asks s6-svscan to stop everything.
SERVICES=/opt/docqa/services
LOCAL="${DOCQA_LOCAL_DIR:-/var/lib/docqa}"
shutdown() {
  trap '' TERM INT
  s6-svc -wD -T 60000 -d "$SERVICES/backup" || true
  # Skip it when the app never came up: a restore may not have finished, and backing up
  # a half-restored database would overwrite the good copy.
  if curl -fsS --max-time 3 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "[shutdown] final backup"
    s6-setuidgid docqa python /opt/docqa/backup.py \
      "${DOCQA_DATA_DIR:-/workspace/docqa}/backups" "$LOCAL/qdrant/snapshots" \
      || echo "[shutdown] final backup failed"
  fi
  s6-svscanctl -t "$SERVICES"
  wait "$svscan" || true
  exit 0
}
trap shutdown TERM INT

s6-svscan "$SERVICES" &
svscan=$!
wait "$svscan"
