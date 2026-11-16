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

exec s6-svscan /opt/docqa/services
