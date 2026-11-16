# Shared by the service run scripts.
#
# Two places hold data:
#   DATA   persistent storage (the Runpod volume at /workspace). It is a network filesystem
#          that ignores chown/chmod, so PostgreSQL cannot run on it and Qdrant warns of
#          corruption. Plain files live here: uploads, model weights, the queue, and backups.
#   LOCAL  the container's own disk: fast and POSIX-correct, but wiped when the pod stops.
#          The databases run here and are restored from DATA/backups at the next boot.
set -euo pipefail

DATA="${DOCQA_DATA_DIR:-/workspace/docqa}"
LOCAL="${DOCQA_LOCAL_DIR:-/var/lib/docqa}"
BACKUPS="$DATA/backups"
export HF_HOME="${HF_HOME:-$DATA/huggingface}"

# s6-supervise starts each run script inside its own service directory, which gives the name.
SERVICE="$(basename "$PWD")"
log() { echo "[$SERVICE] $*"; }

# own <user> <path...>: hand paths to a user where the filesystem allows it. On the Runpod
# volume chown is refused, but everything there is already writable by every user.
own() { local user="$1"; shift; chown -R "$user:$user" "$@" 2>/dev/null || true; }

# wait_for <name> <command...>: block until the command succeeds.
wait_for() {
  local name="$1"; shift
  until "$@" >/dev/null 2>&1; do sleep 1; done
  log "$name is up"
}

http_ok() { curl -fsS --max-time 3 "$1"; }

models_enabled() { [ "${DOCQA_MODELS:-on}" = "on" ]; }

# A service that is switched off must still stay "running" for the supervisor.
idle_forever() { log "$1"; exec sleep infinity; }

# The JWT signing key: supplied through the environment, or the one start.sh generated
# on the volume at first boot.
load_jwt_secret() {
  if [ -z "${DOCQA_JWT_SECRET:-}" ]; then
    DOCQA_JWT_SECRET="$(cat "$DATA/secrets/jwt_secret")"
    export DOCQA_JWT_SECRET
  fi
}
